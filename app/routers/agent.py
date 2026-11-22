import random
from fastapi import APIRouter, HTTPException
from app.agents.graph import negotiation_agent
from app.db import supabase
from app.services.notifications import send_match_notification

router = APIRouter(prefix="/api/agent", tags=["Agentic AI Negotiation Engine"])


def _generate_unique_pin() -> str:
    return f"{random.randint(1000, 9999)}"


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

    result = negotiation_agent.invoke(initial_state)
    is_matched = str(result.get("status", "")).lower() == "matched" and bool(result.get("matched_shelter"))
    matched_shelter = result.get("matched_shelter") or {}
    matched_shelter_id = matched_shelter.get("beneficiary_id")
    matched_shelter_name = matched_shelter.get("organization_name", "Panti Penerima")
    match_pin = _generate_unique_pin() if is_matched else None

    negotiation_id = None
    try:
        res_log = supabase.table("agent_negotiation_logs").insert({
            "listing_id": listing_id,
            "beneficiary_id": matched_shelter_id,
            "status": "Matched" if is_matched else result.get("status", "no_match_found"),
            "match_score": result.get("match_score", 0.0),
            "decision_logs": result.get("log_messages", [])
        }).execute()
        if res_log.data:
            negotiation_id = res_log.data[0].get("id")
    except Exception as log_err:
        print(f"[WARNING] Gagal insert ke agent_negotiation_logs: {log_err}")

    if is_matched:
        supabase.table("food_surplus_listings").update({
            "status": "Matched",
            "matched_beneficiary_id": matched_shelter_id
        }).eq("id", listing_id).execute()

        food_title = result["food_item"].get("food_title", "Surplus Food") if result.get("food_item") else "Surplus Food"
        dist = matched_shelter.get("distance_km", 0.0)
        eta = matched_shelter.get("estimated_eta_hours", 0.0)
        route_poly = matched_shelter.get("route_polyline", [])

        dispatch_data = None
        try:
            pickup_addr = (
                result["food_item"].get("address")
                or result["food_item"].get("organization_name", "Hotel Donor")
                if result.get("food_item")
                else "Hotel Donor"
            )
            delivery_addr = matched_shelter.get("address") or matched_shelter_name
            dist_val = float(dist) if dist != "N/A" else 0.0
            eta_mins = max(1, int(round(float(eta) * 60))) if eta != "N/A" else 15

            res_dispatch = supabase.table("delivery_dispatches").insert({
                "negotiation_id": negotiation_id,
                "listing_id": listing_id,
                "beneficiary_id": matched_shelter_id,
                "pickup_address": pickup_addr,
                "delivery_address": delivery_addr,
                "estimated_distance_km": dist_val,
                "estimated_duration_mins": eta_mins,
                "delivery_status": "Assigned",
                "verification_pin": match_pin,
                "route_polyline": route_poly
            }).execute()
            dispatch_data = res_dispatch.data[0] if res_dispatch.data else None
        except Exception as disp_err:
            print(f"[WARNING] Gagal insert ke delivery_dispatches: {disp_err}")

        try:
            send_match_notification(
                topic="couriers_available",
                title="📦 Penjemputan Makanan Baru!",
                body=f"Jemput '{food_title}' -> Antar ke {matched_shelter_name} (Jarak: {dist} km, PIN: {match_pin})",
                data_payload={
                    "listing_id": listing_id,
                    "shelter_id": str(matched_shelter_id),
                    "verification_pin": str(match_pin)
                }
            )
        except Exception:
            pass

        logs_with_pin = list(result.get("log_messages") or [])
        logs_with_pin.append(f"PIN Handover Unik untuk match ini: {match_pin}")

        return {
            "status": "success",
            "listing_id": listing_id,
            "matched_shelter_id": matched_shelter_id,
            "matched_shelter_name": matched_shelter_name,
            "verification_pin": match_pin,
            "dispatch": dispatch_data,
            "routing_metrics": {
                "distance_km": dist,
                "estimated_eta_hours": eta
            },
            "match_score": result.get("match_score", 0.0),
            "logs": logs_with_pin
        }

    return {
        "status": result.get("status", "no_match_found"),
        "message": "Tidak berhasil menemukan match yang cocok.",
        "logs": result.get("log_messages", [])
    }


@router.post("/match/{listing_id}")
def run_agent_matching(listing_id: str):
    """Menjalankan Agen AI LangGraph untuk memproses match makanan & panti secara otomatis."""
    try:
        return execute_agent_orchestration(listing_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))