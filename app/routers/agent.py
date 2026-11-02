#alur pemanggilan agen oleh backend melalui HTTP REST API
from fastapi import APIRouter, HTTPException
from app.agents.graph import negotiation_agent
from app.db import supabase

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
            matched_shelter_id = result["matched_shelter"]["beneficiary_id"]
            
            # Update status di DB
            supabase.table("food_surplus_listings") \
                .update({"status": "Matched"}) \
                .eq("id", listing_id) \
                .execute()
                
            return {
                "status": "success",
                "listing_id": listing_id,
                "matched_shelter_id": matched_shelter_id,
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