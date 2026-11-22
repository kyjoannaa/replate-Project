import os
import math
from datetime import datetime, timezone
import httpx
from app.db import supabase

OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving"
MAPBOX_TRAFFIC_URL = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _get_real_route_sync(pickup_lat: float, pickup_lon: float, dropoff_lat: float, dropoff_lon: float) -> dict:
    traffic_token = os.getenv("MAPBOX_ACCESS_TOKEN", "").strip()

    with httpx.Client(timeout=8.0) as client:
        if traffic_token:
            try:
                url = f"{MAPBOX_TRAFFIC_URL}/{pickup_lon},{pickup_lat};{dropoff_lon},{dropoff_lat}"
                resp = client.get(
                    url,
                    params={
                        "access_token": traffic_token,
                        "geometries": "geojson",
                        "overview": "full",
                    },
                )
                data = resp.json()
                if data.get("routes"):
                    route = data["routes"][0]
                    coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
                    return {
                        "distance_km": round(route["distance"] / 1000.0, 2),
                        "duration_mins": max(1, round(route["duration"] / 60.0)),
                        "route_polyline": coords,
                    }
            except Exception:
                pass

        try:
            url = f"{OSRM_BASE_URL}/{pickup_lon},{pickup_lat};{dropoff_lon},{dropoff_lat}"
            resp = client.get(
                url,
                params={"overview": "full", "geometries": "geojson"},
            )
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
                return {
                    "distance_km": round(route["distance"] / 1000.0, 2),
                    "duration_mins": max(1, round(route["duration"] / 60.0)),
                    "route_polyline": coords,
                }
        except Exception:
            pass

    dist = round(_haversine_km(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon), 2)
    mins = max(5, round((dist / 25.0) * 60))
    return {
        "distance_km": dist,
        "duration_mins": mins,
        "route_polyline": [[pickup_lat, pickup_lon], [dropoff_lat, dropoff_lon]],
    }


def fetch_data_node(state: dict) -> dict:
    listing_id = state.get("listing_id")
    logs = list(state.get("log_messages") or [])

    listing_res = (
        supabase.table("food_surplus_listings")
        .select("*")
        .eq("id", listing_id)
        .single()
        .execute()
    )
    listing = listing_res.data
    if not listing:
        logs.append("REJECTED: Listing tidak ditemukan.")
        return {**state, "status": "error", "log_messages": logs}

    profiles_res = supabase.table("user_profiles").select("*").execute()
    profile_map = {p["id"]: p for p in (profiles_res.data or [])}

    donor_profile = profile_map.get(listing.get("donor_id")) or {}
    hotel_name = donor_profile.get("organization_name") or donor_profile.get("full_name") or "Hotel Donor"

    food_item = {
        **listing,
        "organization_name": hotel_name,
        "address": donor_profile.get("address") or hotel_name,
        "latitude": donor_profile.get("latitude") if donor_profile.get("latitude") is not None else -6.1702,
        "longitude": donor_profile.get("longitude") if donor_profile.get("longitude") is not None else 106.8310,
    }

    ben_profiles = [p for p in profile_map.values() if p.get("role") == "Beneficiary"]
    constraints_res = supabase.table("beneficiary_constraints").select("*").execute()
    constraint_map = {c["beneficiary_id"]: c for c in (constraints_res.data or [])}

    existing_matches_res = (
        supabase.table("food_surplus_listings")
        .select("matched_beneficiary_id, portion_count, status")
        .in_("status", ["Matched", "Delivered"])
        .execute()
    )
    allocated_portions = {}
    for m in (existing_matches_res.data or []):
        bid = m.get("matched_beneficiary_id")
        if bid:
            allocated_portions[bid] = allocated_portions.get(bid, 0) + (m.get("portion_count") or 0)

    candidates = []
    for prof in ben_profiles:
        bid = prof["id"]
        c = constraint_map.get(bid, {
            "requires_halal": True,
            "requires_vegetarian": False,
            "max_capacity_servings": 50,
        })
        candidates.append({
            **c,
            "beneficiary_id": bid,
            "organization_name": prof.get("organization_name") or prof.get("full_name") or "Beneficiary Shelter",
            "address": prof.get("address") or "",
            "latitude": prof.get("latitude") if prof.get("latitude") is not None else -6.2889,
            "longitude": prof.get("longitude") if prof.get("longitude") is not None else 106.7963,
            "used_portions": allocated_portions.get(bid, 0),
        })

    logs.append(f"Berhasil ambil listing '{listing['food_title']}' ({hotel_name}) & {len(candidates)} kandidat panti.")
    return {
        **state,
        "food_item": food_item,
        "candidate_shelters": candidates,
        "log_messages": logs,
    }


def temporal_filter_node(state: dict) -> dict:
    if state.get("status") == "error":
        return state

    food_item = state.get("food_item") or {}
    candidates = state.get("candidate_shelters") or []
    logs = list(state.get("log_messages") or [])

    exp_dt = datetime.fromisoformat(food_item["expiration_time"].replace("Z", "+00:00"))
    now_dt = datetime.now(timezone.utc)
    remaining_hours = (exp_dt - now_dt).total_seconds() / 3600.0

    if remaining_hours <= 0.15:
        logs.append(f"REJECTED: Makanan hampir kadaluwarsa! Sisa waktu: {remaining_hours:.2f} jam.")
        return {
            **state,
            "status": "expired_risk",
            "eligible_shelters": [],
            "log_messages": logs,
        }

    cat_lower = (food_item.get("category") or "").lower()
    title_lower = (food_item.get("food_title") or "").lower()
    is_non_halal = any(w in cat_lower or w in title_lower for w in ["non-halal", "non halal", "babi", "pork", "lard"])
    is_halal = not is_non_halal and any(w in cat_lower for w in ["halal", "protein", "bakery", "fruit", "vegetarian"])
    is_veggie = any(w in cat_lower for w in ["vegetarian", "veggie", "fruit"])

    eligible = []
    for s in candidates:
        if s.get("requires_halal") and not is_halal:
            continue
        if s.get("requires_vegetarian") and not is_veggie:
            continue
        max_cap = s.get("max_capacity_servings") or 50
        remaining_cap = max(0, max_cap - (s.get("used_portions") or 0))
        if remaining_cap <= 0:
            continue
        eligible.append({**s, "remaining_cap": remaining_cap, "remaining_hours": remaining_hours})

    logs.append(f"PASSED TEMPORAL CHECK: Sisa waktu {remaining_hours:.2f} jam ({len(eligible)} panti memenuhi syarat).")
    return {
        **state,
        "eligible_shelters": eligible,
        "log_messages": logs,
    }


def dietary_and_capacity_matching_node(state: dict) -> dict:
    if state.get("status") in ("error", "expired_risk"):
        return state

    food_item = state.get("food_item") or {}
    eligible = state.get("eligible_shelters") or []
    logs = list(state.get("log_messages") or [])

    if not eligible:
        logs.append("REJECTED: Tidak ada panti yang cocok dengan kriteria gizi/halal/kapasitas tersisa.")
        return {
            **state,
            "status": "no_match_found",
            "matched_shelter": None,
            "match_score": 0.0,
            "log_messages": logs,
        }

    pickup_lat = food_item.get("latitude", -6.1702)
    pickup_lon = food_item.get("longitude", 106.8310)
    portions = food_item.get("portion_count") or 1

    scored = []
    for s in eligible:
        max_cap = s.get("max_capacity_servings") or 50
        rem_cap = s.get("remaining_cap", max_cap)
        straight_km = _haversine_km(pickup_lat, pickup_lon, s["latitude"], s["longitude"])

        headroom_score = (rem_cap / max_cap) * 50.0
        fit_score = min(portions / max(rem_cap, 1), 1.0) * 30.0
        proximity_score = max(0.0, 20.0 - straight_km)
        total_score = round(headroom_score + fit_score + proximity_score, 1)
        scored.append({**s, "score": total_score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]

    route_info = _get_real_route_sync(pickup_lat, pickup_lon, best["latitude"], best["longitude"])
    eta_hours = round(route_info["duration_mins"] / 60.0, 2)
    remaining_hours = best.get("remaining_hours", 1.0)

    if remaining_hours <= (eta_hours + 0.15):
        logs.append(
            f"REJECTED: Makanan hampir kadaluwarsa! Sisa waktu {remaining_hours:.2f} jam < ETA OSRM {route_info['duration_mins']} mnt."
        )
        return {
            **state,
            "status": "expired_risk",
            "matched_shelter": None,
            "match_score": 0.0,
            "log_messages": logs,
        }

    matched_shelter = {
        **best,
        "distance_km": route_info["distance_km"],
        "estimated_eta_hours": eta_hours,
        "route_polyline": route_info["route_polyline"],
    }

    logs.append(
        f"MATCH SUCCESS: Matched dengan '{best['organization_name']}' (Skor: {best['score']}, Jarak: {route_info['distance_km']} km, Sisa Kapasitas: {best['remaining_cap']} porsi)."
    )

    return {
        **state,
        "status": "matched",
        "matched_shelter": matched_shelter,
        "match_score": best["score"],
        "log_messages": logs,
    }