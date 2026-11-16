#alur pemanggilan agen oleh backend melalui HTTP REST API
from fastapi import APIRouter, Depends, HTTPException
from app.agents.graph import negotiation_agent
from app.db import supabase
from app.services.notifications import send_match_notification
from app.dependencies.auth import require_role

router = APIRouter(prefix="/api/agent", tags=["Agentic AI Negotiation Engine"])


def execute_agent_orchestration(listing_id: str) -> dict:
    """Fungsi inti untuk menjalankan Agen AI LangGraph, menyimpan log & dispatch, serta mengirim notifikasi."""
    initial_state = {
        "listing_id": listing_id,
        "food_item": None,
        "candidate_shelters": [],
        "eligible_shelters": [],
        "matched_shelter": None,
        "match_score": 0.0,
        "status": "processing",
        "log_messages": []
    }
    
    # Jalankan agen LangGraph
    result = negotiation_agent.invoke(initial_state)
    is_matched = result["status"].lower() == "matched" and bool(result.get("matched_shelter"))
    matched_shelter = result.get("matched_shelter") or {}
    matched_shelter_id = matched_shelter.get("beneficiary_id")

    # [BARU] Simpan trace keputusan agen ke tabel agent_negotiation_logs (aman dengan try-except)
    negotiation_id = None   
    try:
        res_log = supabase.table("agent_negotiation_logs").insert({
            "listing_id": listing_id,
            "beneficiary_id": matched_shelter_id,
            "status": "Matched" if is_matched else result["status"],
            "match_score": result.get("match_score", 0.0),
            "decision_logs": result.get("log_messages", [])
        }).execute()
        if res_log.data:
            negotiation_id = res_log.data[0].get("id")
    except Exception as log_err:
        print(f"[WARNING] Gagal insert ke agent_negotiation_logs: {log_err}")

    # Jika berhasil match, update status listing di Supabase
    if is_matched:
        # Update status di DB
        supabase.table("food_surplus_listings") \
            .update({"status": "Matched"}) \
            .eq("id", listing_id) \
            .execute()

        # Kirim notifikasi push via FCM ke kurir yang tersedia
        food_title = result["food_item"].get("food_title", "Surplus Food") if result.get("food_item") else "Surplus Food"
        dist = matched_shelter.get("distance_km", "N/A")
        eta = matched_shelter.get("estimated_eta_hours", "N/A")

        # [BARU] Buat record pengiriman otomatis di tabel delivery_dispatches (sesuai skema temanmu)
        dispatch_data = None
        try:
            pickup_addr = result["food_item"].get("organization_name", "Hotel Donor") if result.get("food_item") else "Hotel Donor"
            delivery_addr = matched_shelter.get("organization_name", "Panti Penerima")
            dist_val = float(dist) if dist != "N/A" else 0.0
            eta_mins = int(round(float(eta) * 60)) if eta != "N/A" else 0

            res_dispatch = supabase.table("delivery_dispatches").insert({
                "negotiation_id": negotiation_id,
                "pickup_address": pickup_addr,
                "delivery_address": delivery_addr,
                "estimated_distance_km": dist_val,
                "estimated_duration_mins": eta_mins,
                "delivery_status": "Assigned"
            }).execute()
            dispatch_data = res_dispatch.data[0] if res_dispatch.data else None
        except Exception as disp_err:
            print(f"[WARNING] Gagal insert ke delivery_dispatches: {disp_err}")
        
        send_match_notification(
            topic="couriers_available",
            title="📦 Penjemputan Makanan Baru!",
            body=f"Jemput '{food_title}' -> Antar ke Panti (Jarak: {dist} km, ETA: {eta} jam)",
            data_payload={
                "listing_id": listing_id,
                "shelter_id": str(matched_shelter_id)
            }
        )
            
        return {
            "status": "success",
            "listing_id": listing_id,
            "matched_shelter_id": matched_shelter_id,
            "dispatch": dispatch_data,
            "routing_metrics": {
                "distance_km": dist,
                "estimated_eta_hours": eta
            },
            "match_score": result["match_score"],
            "logs": result["log_messages"]
        }
    else:
        return {
            "status": result["status"],
            "message": "Tidak berhasil menemukan match yang cocok.",
            "logs": result["log_messages"]
        }


@router.post("/match/{listing_id}")
def run_agent_matching(
    listing_id: str,
    current_user: dict = Depends(require_role("Donor"))
    ):
    """Menjalankan Agen AI LangGraph untuk memproses match makanan & panti secara otomatis."""
    try:
        return execute_agent_orchestration(listing_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))