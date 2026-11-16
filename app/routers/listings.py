# endpoint to query the mock surplus food data
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from datetime import datetime
from app.db import supabase
from app.routers.agent import execute_agent_orchestration
from typing import Optional
from app.dependencies.auth import require_role
from fastapi import APIRouter, Depends, HTTPException, status

router = APIRouter(prefix="/api/listings", tags=["Food Surplus Listings"])

# Input model for creating a new food listing
class FoodListingCreate(BaseModel):
    donor_id: str # Harus berisi string dengan format UUID
    food_title: str
    category: Optional[str] = None
    weight_kg: Optional[float] = None
    portion_count: Optional[int] = None
    cooked_time: Optional[datetime] = None
    expiration_time: datetime

# --- FUNGSI INPUT POST YANG BARU ---
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_food_listing(
    listing: FoodListingCreate,
    current_user: dict = Depends(require_role("Donor"))
):
    """Add a new food surplus listing."""
    try:
        # Menyiapkan payload yang sama persis dengan kolom Supabase
        listing_data = {
            "donor_id": listing.donor_id,
            "food_title": listing.food_title,
            "category": listing.category,
            "weight_kg": listing.weight_kg,
            "portion_count": listing.portion_count,
            "cooked_time": listing.cooked_time.isoformat() if listing.cooked_time else None,
            "expiration_time": listing.expiration_time.isoformat(),
            "status": "Pending" 
        }
        
        response = supabase.table("food_surplus_listings").insert(listing_data).execute()

        # [BARU] Jalankan agen AI secara otomatis setelah listing tersimpan
        agent_result = None
        if response.data:
            new_listing_id = response.data[0]["id"]
            try:
                agent_result = execute_agent_orchestration(new_listing_id)
                if agent_result and agent_result.get("status") == "success":
                    response.data[0]["status"] = "Matched"
            except Exception as agent_err:
                agent_result = {"status": "error", "message": str(agent_err)}
        
        return {
            "status": "success",
            "message": "Data makanan surplus berhasil ditambahkan!",
            "data": response.data,
            "agent_matching": agent_result
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal menyimpan data: {str(e)}"
        )

@router.get("/")
def get_all_listings():
    """Fetch all surplus food listings from Supabase."""
    try:
        response = supabase.table("food_surplus_listings").select("*").execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{listing_id}")
async def get_listing_by_id(listing_id: str):
    """Fetch a specific food listing by ID."""
    try:
        response = supabase.table("food_surplus_listings").select("*").eq("id", listing_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Listing not found")
        return {"status": "success", "data": response.data[0]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))