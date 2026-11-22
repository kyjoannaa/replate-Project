import math
import random
from datetime import datetime, timezone
from app.core.supabase import supabase
from app.services.routing import get_real_route, haversine_km


def _generate_unique_pin() -> str:
    return f"{random.randint(1000, 9999)}"


async def run_matching_workflow(listing_id: str) -> dict:
    logs = []

    listing_res = (
        supabase.table("food_surplus_listings")
        .select("*")
        .eq("id", listing_id)
        .single()
        .execute()
    )
    listing = listing_res.data
    if not listing:
        return {"status": "error", "logs": ["Listing tidak ditemukan."]}

    profiles_res = supabase.table("user_profiles").select("*").execute()
    profile_map = {p["id"]: p for p in (profiles_res.data or [])}

    donor_profile = profile_map.get(listing.get("donor_id")) or {}
    pickup_lat = donor_profile.get("latitude") if donor_profile.get("latitude") is not None else -6.1702
    pickup_lon = donor_profile.get("longitude") if donor_profile.get("longitude") is not None else 106.8310
    hotel_name = donor_profile.get("organization_name") or donor_profile.get("full_name") or "Hotel Donor"

    # Fetch all Beneficiary profiles + their saved constraints
    ben_profiles = [p for p in profile_map.values() if p.get("role") == "Beneficiary"]
    constraints_res = supabase.table("beneficiary_constraints").select("*").execute()
    constraint_map = {c["beneficiary_id"]: c for c in (constraints_res.data or [])}

    # Calculate already-allocated portions per shelter so load balances across shelters
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

    logs.append(f"Berhasil ambil listing '{listing['food_title']}' ({hotel_name}) & {len(ben_profiles)} kandidat panti.")

    if not ben_profiles:
        logs.append("REJECTED: Belum ada akun Beneficiary/Panti yang terdaftar.")
        return {"status": "no_match_found", "logs": logs}

    exp_dt = datetime.fromisoformat(listing["expiration_time"].replace("Z", "+00:00"))
    remaining_hours = (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
    if remaining_hours <= 0.05:
        logs.append(f"REJECTED: Makanan sudah melewati batas aman ({remaining_hours:.2f} jam).")
        return {"status": "expired_risk", "logs": logs}

    cat_lower = (listing.get("category") or "").lower()
    title_lower = (listing.get("food_title") or "").lower()
    is_non_halal = any(w in cat_lower or w in title_lower for w in ["non-halal", "non halal", "babi", "pork", "lard"])
    is_halal = not is_non_halal and any(w in cat_lower for w in ["halal", "protein", "bakery", "fruit", "vegetarian"])
    is_veggie = any(w in cat_lower for w in ["vegetarian", "veggie", "fruit"])

    candidates = []
    for prof in ben_profiles:
        bid = prof["id"]
        c = constraint_map.get(bid, {
            "requires_halal": True,
            "requires_vegetarian": False,
            "max_capacity_servings": 50,
        })

        if c.get("requires_halal") and not is_halal:
            continue
        if c.get("requires_vegetarian") and not is_veggie:
            continue

        max_cap = c.get("max_capacity_servings") or 50
        used_cap = allocated_portions.get(bid, 0)
        remaining_cap = max(0, max_cap - used_cap)

        if remaining_cap <= 0:
            continue

        drop_lat = prof.get("latitude") if prof.get("latitude") is not None else -6.2889
        drop_lon = prof.get("longitude") if prof.get("longitude") is not None else 106.7963
        straight_km = haversine_km(pickup_lat, pickup_lon, drop_lat, drop_lon)

        # Score: 50 pts remaining capacity headroom + 30 pts portion fit + 20 pts proximity
        headroom_score = (remaining_cap / max_cap) * 50.0
        fit_score = min(listing["portion_count"] / max(remaining_cap, 1), 1.0) * 30.0
        proximity_score = max(0.0, 20.0 - straight_km)
        total_score = round(headroom_score + fit_score + proximity_score, 1)

        candidates.append({
            "profile": prof,
            "constraints": c,
            "remaining_cap": remaining_cap,
            "drop_lat": drop_lat,
            "drop_lon": drop_lon,
            "score": total_score,
        })

    if not candidates:
        logs.append("REJECTED: Tidak ada panti yang memenuhi kriteria halal/gizi atau kapasitas tersisa.")
        return {"status": "no_match_found", "logs": logs}

    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]
    best_prof = best["profile"]
    shelter_id = best_prof["id"]
    shelter_name = best_prof.get("organization_name") or best_prof.get("full_name") or "Beneficiary Shelter"

    route_info = await get_real_route(pickup_lat, pickup_lon, best["drop_lat"], best["drop_lon"])
    eta_hours = route_info["duration_mins"] / 60.0

    if remaining_hours <= (eta_hours + 0.15):
        logs.append(
            f"REJECTED: Makanan hampir kadaluwarsa! Sisa waktu {remaining_hours:.2f} jam < waktu tempuh OSRM {route_info['duration_mins']} mnt."
        )
        return {"status": "expired_risk", "logs": logs}

    logs.append(
        f"PASSED TEMPORAL CHECK: Sisa waktu {remaining_hours:.2f} jam > ETA OSRM {route_info['duration_mins']} mnt ({route_info['distance_km']} km)."
    )

    supabase.table("food_surplus_listings").update({
        "status": "Matched",
        "matched_beneficiary_id": shelter_id,
    }).eq("id", listing_id).execute()

    match_pin = _generate_unique_pin()
    dispatch_payload = {
        "listing_id": listing_id,
        "beneficiary_id": shelter_id,
        "pickup_address": donor_profile.get("address") or hotel_name,
        "delivery_address": best_prof.get("address") or shelter_name,
        "estimated_distance_km": route_info["distance_km"],
        "estimated_duration_mins": route_info["duration_mins"],
        "delivery_status": "Assigned",
        "verification_pin": match_pin,
        "route_polyline": route_info["route_polyline"],
    }
    disp_res = supabase.table("delivery_dispatches").insert(dispatch_payload).execute()
    created_dispatch = disp_res.data[0] if disp_res.data else dispatch_payload

    logs.append(
        f"MATCH SUCCESS: Matched dengan '{shelter_name}' (Skor: {best['score']}, Sisa Kapasitas: {best['remaining_cap']} porsi, PIN: {match_pin})."
    )

    return {
        "status": "success",
        "matched_shelter_id": shelter_id,
        "matched_shelter_name": shelter_name,
        "beneficiary_address": best_prof.get("address") or "",
        "verification_pin": match_pin,
        "dispatch": created_dispatch,
        "logs": logs,
    }