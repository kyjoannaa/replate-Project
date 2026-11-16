from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel
from typing import Optional
from app.db import supabase
from app.dependencies.auth import require_role

router = APIRouter(prefix="/api/impact", tags=["Impact Logs"])

class ImpactLogCreate(BaseModel):
    dispatch_id: Optional[str] = None
    weight_rescued_kg: Optional[float] = None
    carbon_offset_kg: Optional[float] = None

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_impact_log(
    impact: ImpactLogCreate,
    current_user: dict = Depends(require_role("Courier", "Beneficiary"))
    ):
    """Log the environmental impact of a completed delivery."""
    try:
        impact_data = impact.dict()
        response = supabase.table("impact_logs").insert(impact_data).execute()
        
        return {
            "status": "success",
            "message": "Impact Log berhasil dicatat!",
            "data": response.data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mencatat impact log: {str(e)}")