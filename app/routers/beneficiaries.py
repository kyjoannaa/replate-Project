#endpoint to fetch shelter and beneficiarie profiles
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from app.db import supabase

router = APIRouter(prefix="/api/beneficiaries", tags=["Beneficiaries"])

class BeneficiaryConstraintCreate(BaseModel):
    beneficiary_id: str
    requires_halal: Optional[bool] = False
    requires_vegetarian: Optional[bool] = False
    max_capacity_servings: Optional[int] = 0
    operating_hours: Optional[str] = None

@router.get("/")
def get_beneficiaries():
    """Fetch all beneficiary shelters and their dietary constraints."""
    try:
        response = supabase.table("beneficiary_constraints").select("*, profiles(organization_name, address)").execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_beneficiary_constraint(constraint: BeneficiaryConstraintCreate):
    """Add new constraints and preferences for a beneficiary."""
    try:
        constraint_data = constraint.dict()
        response = supabase.table("beneficiary_constraints").insert(constraint_data).execute()
        
        return {
            "status": "success",
            "message": "Kebutuhan berhasil ditambahkan!",
            "data": response.data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan data: {str(e)}")