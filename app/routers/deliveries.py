from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from app.db import supabase

router = APIRouter(prefix="/api/deliveries", tags=["Deliveries"])

class DeliveryDispatchCreate(BaseModel):
    negotiation_id: Optional[str] = None
    courier_id: Optional[str] = None
    pickup_address: str
    delivery_address: str
    estimated_distance_km: Optional[float] = None
    estimated_duration_mins: Optional[int] = None

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_delivery(delivery: DeliveryDispatchCreate):
    """Assign a delivery dispatch to a courier."""
    try:
        delivery_data = delivery.dict()
        delivery_data["delivery_status"] = "Assigned"
        
        response = supabase.table("delivery_dispatches").insert(delivery_data).execute()
        
        return {
            "status": "success",
            "message": "Penugasan kurir berhasil dibuat!",
            "data": response.data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal menugaskan kurir: {str(e)}")