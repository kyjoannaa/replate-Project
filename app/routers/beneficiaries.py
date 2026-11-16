# endpoint to fetch shelter and beneficiary profiles
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from app.db import supabase
from app.dependencies.auth import require_role

router = APIRouter(prefix="/api/beneficiaries", tags=["Beneficiaries"])


class BeneficiaryConstraintCreate(BaseModel):
    beneficiary_id: str
    requires_halal: Optional[bool] = False
    requires_vegetarian: Optional[bool] = False
    max_capacity_servings: Optional[int] = 0
    operating_hours: Optional[str] = None


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_beneficiary_constraint(
    beneficiary: BeneficiaryConstraintCreate,
    current_user: dict = Depends(require_role("Beneficiary"))
):
    """Create dietary and capacity constraints for a Beneficiary."""
    try:
        data = beneficiary.dict()
        response = supabase.table("beneficiary_constraints").insert(data).execute()
        return {
            "status": "success",
            "message": "Data constraints penerima manfaat berhasil disimpan!",
            "data": response.data
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal menyimpan data beneficiary: {str(e)}"
        )


@router.get("/")
def get_beneficiaries():
    """Fetch all shelter constraints enriched with user_profiles."""
    try:
        # Ambil data constraints tanpa relasi 'profiles' agar tidak memicu error PGRST200
        res_shelters = supabase.table("beneficiary_constraints").select("*").execute()
        
        # Ambil data profil organisasi dari tabel 'user_profiles'
        res_profiles = supabase.table("user_profiles").select("id, user_id, organization_name, address, latitude, longitude").execute()
        profiles_map = {}
        for p in (res_profiles.data or []):
            if p.get("user_id"):
                profiles_map[p["user_id"]] = p
            if p.get("id"):
                profiles_map[p["id"]] = p

        enriched_data = []
        for shelter in (res_shelters.data or []):
            shelter_copy = shelter.copy()
            prof = profiles_map.get(shelter.get("beneficiary_id"))
            if prof:
                shelter_copy["user_profiles"] = prof
            enriched_data.append(shelter_copy)

        return {"status": "success", "data": enriched_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))