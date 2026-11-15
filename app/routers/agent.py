#alur pemanggilan agen oleh backend melalui HTTP REST API
from unittest import result

from fastapi import APIRouter, HTTPException
from app.agents.graph import negotiation_agent
from app.db import supabase
from app.services.notifications import send_match_notification

router = APIRouter(prefix="/api/agent", tags=["Agentic AI Negotiation Engine"])

@router.post("/match/{listing_id}")
def run_agent_matching(listing_id: str):
    """Menjalankan Agen AI LangGraph untuk memproses match makanan & panti secara otomatis."""
    try:
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

        # Jika berhasil match, update status listing di Supabase
        if result["status"].lower() == "matched" and result["matched_shelter"]:
            matched_shelter = result["matched_shelter"]  # <-- Tambahkan baris ini
            matched_shelter_id = matched_shelter["beneficiary_id"]
            
            # Update status di DB
            supabase.table("food_surplus_listings") \
                .update({"status": "Matched"}) \
                .eq("id", listing_id) \
                .execute()

            # Kirim notifikasi push via FCM ke kurir yang tersedia
            food_title = result["food_item"].get("food_title", "Surplus Food") if result.get("food_item") else "Surplus Food"
            dist = matched_shelter.get("distance_km", "N/A")
            eta = matched_shelter.get("estimated_eta_hours", "N/A")
            
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))