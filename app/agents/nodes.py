#fungsi fungsi keputusan agent
from datetime import datetime, timezone
from app.db import supabase
from app.agents.state import AgentState
from app.services.routing import calculate_delivery_eta

def fetch_data_node(state: AgentState) -> AgentState:
    """Node 1: Ambil data listing makanan dan semua data panti penerima"""
    listing_id = state["listing_id"]
    logs = list(state.get("log_messages", []))
    
    # Ambil data makanan
    res_food = supabase.table("food_surplus_listings").select("*").eq("id", listing_id).execute()
    if not res_food.data:
        logs.append(f"Listing {listing_id} tidak ditemukan.")
        return {**state, "status": "failed", "log_messages": logs}
    
    food_item = res_food.data[0]
    
    # Ambil data koordinat & nama organisasi dari tabel user_profiles
    res_profiles = supabase.table("user_profiles").select("id, user_id, organization_name, latitude, longitude").execute()
    profiles_map = {}
    for p in (res_profiles.data or []):
        if p.get("user_id"):
            profiles_map[p["user_id"]] = p
        if p.get("id"):
            profiles_map[p["id"]] = p

    # Pasang koordinat donor hotel
    donor_prof = profiles_map.get(food_item.get("donor_id"), {})
    food_item["organization_name"] = donor_prof.get("organization_name", "Hotel Donor")
    food_item["latitude"] = donor_prof.get("latitude") or food_item.get("latitude") or -6.1754
    food_item["longitude"] = donor_prof.get("longitude") or food_item.get("longitude") or 106.8272
    
    # Ambil data panti penerima
    res_shelters = supabase.table("beneficiary_constraints").select("*").execute()
    
    candidate_shelters = []
    for shelter in res_shelters.data:
        ben_prof = profiles_map.get(shelter.get("beneficiary_id"), {})
        shelter_copy = shelter.copy()
        shelter_copy["organization_name"] = ben_prof.get("organization_name", "Panti Target")
        shelter_copy["latitude"] = ben_prof.get("latitude") or shelter.get("latitude") or -6.2088
        shelter_copy["longitude"] = ben_prof.get("longitude") or shelter.get("longitude") or 106.8456
        candidate_shelters.append(shelter_copy)
    
    logs.append(f"Berhasil ambil data listing '{food_item['food_title']}' & {len(candidate_shelters)} candidate panti.")
    return {
        **state,
        "food_item": food_item,
        "candidate_shelters": candidate_shelters,
        "log_messages": logs
    }


def temporal_filter_node(state: AgentState) -> AgentState:
    """Node 2: Cek apakah makanan masih layak sebelum expired (T_current + ETA <= T_expiration)"""
    food_item = state["food_item"]
    logs = list(state["log_messages"])
    
    if not food_item:
        return state

    # Parse ISO timestamp expiration
    exp_time_str = food_item["expiration_time"].replace("Z", "+00:00")
    exp_time = datetime.fromisoformat(exp_time_str)
    now = datetime.now(timezone.utc)
    
    sisa_waktu_jam = (exp_time - now).total_seconds() / 3600.0

    # Ambil koordinat donor hotel
    donor_lat = food_item.get("latitude", -6.1754)
    donor_lng = food_item.get("longitude", 106.8272)
    
    # Estimasi waktu pengiriman buffer (misal 1.0 jam untuk transit)
    estimated_eta_hours = 1.0

    # Kalkulasi ETA & jarak secara dinamis ke setiap kandidat panti via OSRM API
    updated_candidates = []
    for shelter in state["candidate_shelters"]:
        s_lat = shelter.get("latitude", -6.2088)
        s_lng = shelter.get("longitude", 106.8456)
        
        # Panggil API OSRM
        route_info = calculate_delivery_eta(donor_lat, donor_lng, s_lat, s_lng)
        estimated_eta = route_info["duration_hours"]
        
        # Filter: T_current + ETA <= T_expiration
        if sisa_waktu_jam > estimated_eta:
            shelter_copy = shelter.copy()
            shelter_copy["estimated_eta_hours"] = estimated_eta
            shelter_copy["distance_km"] = route_info["distance_km"]
            updated_candidates.append(shelter_copy)
    
    if not updated_candidates:
        logs.append(f"REJECTED: Makanan hampir kadaluwarsa! Sisa waktu: {sisa_waktu_jam:.2f} jam.")
        return {**state, "status": "expired_risk", "log_messages": logs}
        
    logs.append(f"PASSED TEMPORAL CHECK: {len(updated_candidates)} panti dapat dijangkau sebelum kadaluwarsa (Sisa waktu: {sisa_waktu_jam:.2f} jam).")
    return {**state, "candidate_shelters": updated_candidates, "log_messages": logs}


def dietary_and_capacity_matching_node(state: AgentState) -> AgentState:
    """Node 3: Filter Halal, Vegetarian, dan Kalkulasi Porsi"""
    food_item = state["food_item"]
    candidates = state["candidate_shelters"]
    logs = list(state["log_messages"])
    
    if state.get("status") == "expired_risk":
        return state

    category = food_item.get("category", "").lower()
    portions_available = food_item.get("portion_count", 0)
    
    is_food_halal = "halal" in category or "protein" in category or "bakery" in category or "fruit" in category
    is_food_veggie = "veggie" in category or "vegetarian" in category or "fruit" in category

    eligible = []
    
    for shelter in candidates:
        req_halal = shelter.get("requires_halal", False)
        req_veggie = shelter.get("requires_vegetarian", False)
        max_cap = shelter.get("max_capacity_servings", 0)

        # Cek Aturan Gizi
        if req_halal and not is_food_halal:
            continue
        if req_veggie and not is_food_veggie:
            continue
            
        # Kalkulasi Porsi
        if max_cap > 0:
            score = min(portions_available / max_cap, 1.0) * 100.0
            shelter_info = shelter.copy()
            shelter_info["calculated_score"] = score
            eligible.append(shelter_info)

    # Sort berdasarkan skor matching tertinggi
    eligible.sort(key=lambda x: x["calculated_score"], reverse=True)
    
    if not eligible:
        logs.append("REJECTED: Tidak ada panti yang cocok dengan kriteria gizi/kapasitas.")
        return {**state, "status": "no_match_found", "log_messages": logs}

    best_match = eligible[0]
    beneficiary_id = best_match.get("beneficiary_id", "Panti Target")
    logs.append(f"MATCH SUCCESS: Matched dengan Beneficiary ID '{beneficiary_id}' (Skor: {best_match['calculated_score']:.1f}).")

    return {
        **state,
        "eligible_shelters": eligible,
        "matched_shelter": best_match,
        "match_score": best_match["calculated_score"],
        "status": "Matched",
        "log_messages": logs
    }