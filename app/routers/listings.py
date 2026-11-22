import inspect
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.db import supabase
from app.routers.agent import execute_agent_orchestration

router = APIRouter(prefix="/api/listings", tags=["Food Surplus Listings"])


class FoodListingCreate(BaseModel):
    donor_id: str
    food_title: str
    category: Optional[str] = None
    batch_date: Optional[str] = None
    weight_kg: Optional[float] = None
    portion_count: Optional[int] = None
    cooked_time: Optional[datetime] = None
    expiration_time: datetime


def _enrich_listings(listings: list) -> list:
    if not listings:
        return []

    profiles_res = supabase.table("user_profiles").select("*").execute()
    dispatches_res = supabase.table("delivery_dispatches").select("*").execute()

    profile_map = {p["id"]: p for p in (profiles_res.data or [])}
    dispatch_by_listing = {
        d["listing_id"]: d
        for d in (dispatches_res.data or [])
        if d.get("listing_id")
    }

    enriched = []
    for item in listings:
        donor = profile_map.get(item.get("donor_id")) or {}
        ben = profile_map.get(item.get("matched_beneficiary_id")) or {}
        disp = dispatch_by_listing.get(item["id"]) or {}

        enriched.append({
            **item,
            "hotel_name": donor.get("organization_name") or donor.get("full_name") or "Hotel Donor",
            "hotel_address": donor.get("address") or "",
            "matched_beneficiary": ben.get("organization_name") or ben.get("full_name"),
            "beneficiary_address": ben.get("address") or "",
            "verification_pin": disp.get("verification_pin"),
            "distance_km": disp.get("estimated_distance_km"),
            "eta_mins": disp.get("estimated_duration_mins"),
            "delivery_status": disp.get("delivery_status"),
        })
    return enriched


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_food_listing(listing: FoodListingCreate):
    """Add a new food surplus listing and trigger LangGraph matching."""
    try:
        donor_check = (
            supabase.table("user_profiles")
            .select("id, role")
            .eq("id", listing.donor_id)
            .execute()
        )
        if not donor_check.data or donor_check.data[0].get("role") != "Donor":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only registered Donor accounts can post food listings."
            )

        listing_data = {
            "donor_id": listing.donor_id,
            "food_title": listing.food_title,
            "category": listing.category,
            "batch_date": listing.batch_date or date.today().isoformat(),
            "weight_kg": listing.weight_kg,
            "portion_count": listing.portion_count,
            "cooked_time": listing.cooked_time.isoformat() if listing.cooked_time else None,
            "expiration_time": listing.expiration_time.isoformat(),
            "status": "Pending"
        }

        response = supabase.table("food_surplus_listings").insert(listing_data).execute()

        agent_result = None
        if response.data:
            new_listing_id = response.data[0]["id"]
            try:
                maybe_coro = execute_agent_orchestration(new_listing_id)
                agent_result = await maybe_coro if inspect.isawaitable(maybe_coro) else maybe_coro

                if agent_result and agent_result.get("status") == "success":
                    response.data[0]["status"] = "Matched"
                    response.data[0]["matched_beneficiary_id"] = agent_result.get("matched_shelter_id")
                    response.data[0]["matched_beneficiary"] = agent_result.get("matched_shelter_name")
                    response.data[0]["verification_pin"] = agent_result.get("verification_pin")
            except Exception as agent_err:
                agent_result = {"status": "error", "message": str(agent_err)}

        return {
            "status": "success",
            "message": "Data makanan surplus berhasil ditambahkan!",
            "data": response.data,
            "agent_matching": agent_result
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal menyimpan data: {str(e)}"
        )


@router.get("/")
def get_all_listings():
    """Fetch all surplus food listings enriched with donor, beneficiary, and PIN details."""
    try:
        response = (
            supabase.table("food_surplus_listings")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        enriched = _enrich_listings(response.data or [])
        return {"status": "success", "data": enriched}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{listing_id}")
async def get_listing_by_id(listing_id: str):
    """Fetch a specific food listing by ID."""
    try:
        response = supabase.table("food_surplus_listings").select("*").eq("id", listing_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Listing not found")
        enriched = _enrich_listings(response.data)
        return {"status": "success", "data": enriched[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))