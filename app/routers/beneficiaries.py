#endpoint to fetch shelter and beneficiarie profiles
from fastapi import APIRouter, HTTPException
from app.db import supabase

router = APIRouter(prefix="/api/beneficiaries", tags=["Beneficiaries"])

@router.get("/")
async def get_beneficiaries():
    """Fetch all beneficiary shelters and their dietary constraints."""
    try:
        response = supabase.table("beneficiary_constraints").select("*, profiles(organization_name, address)").execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))