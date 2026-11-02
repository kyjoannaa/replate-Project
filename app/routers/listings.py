#endpoint to query the mock surplus food data
from fastapi import APIRouter, HTTPException
from app.db import supabase

router = APIRouter(prefix="/api/listings", tags=["Food Surplus Listings"])

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