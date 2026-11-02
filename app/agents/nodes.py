#fungsi fungsi keputusan agent
from datetime import datetime, timezone
from app.db import supabase
from app.agents.state import AgentState

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
    
    # Ambil data panti penerima (tanpa relasi profiles agar tidak memicu error PGRST200)
    res_shelters = supabase.table("beneficiary_constraints").select("*").execute()
    
    logs.append(f"Berhasil ambil data listing '{food_item['food_title']}' & {len(res_shelters.data)} candidate panti.")
    return {
        **state,
        "food_item": food_item,
        "candidate_shelters": res_shelters.data,
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
    
    # Estimasi waktu pengiriman buffer (misal 1.0 jam untuk transit)
    estimated_eta_hours = 1.0
    
    if sisa_waktu_jam <= estimated_eta_hours:
        logs.append(f"REJECTED: Makanan hampir kadaluwarsa! Sisa waktu: {sisa_waktu_jam:.2f} jam.")
        return {**state, "status": "expired_risk", "log_messages": logs}
        
    logs.append(f"PASSED TEMPORAL CHECK: Sisa waktu aman ({sisa_waktu_jam:.2f} jam).")
    return {**state, "log_messages": logs}


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
        "status": "matched",
        "log_messages": logs
    }