import inspect
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status
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


def _resolve_donor_profile(raw_donor_id: str) -> Optional[dict]:
    """
    Resolves a Donor's user_profiles row whether raw_donor_id is:
    1) user_profiles.id
    2) user_profiles.user_id (public.users.id)
    3) A public.users.id whose user_profiles row hasn't been initialized yet
    """
    if not raw_donor_id:
        return None

    res_prof_id = supabase.table("user_profiles").select("*").eq("id", raw_donor_id).execute()
    profile = res_prof_id.data[0] if res_prof_id.data else None

    if not profile:
        res_prof_uid = supabase.table("user_profiles").select("*").eq("user_id", raw_donor_id).execute()
        profile = res_prof_uid.data[0] if res_prof_uid.data else None

    linked_user_id = profile.get("user_id") if profile else raw_donor_id
    user_row = None
    if linked_user_id:
        res_user = supabase.table("users").select("*").eq("id", linked_user_id).execute()
        user_row = res_user.data[0] if res_user.data else None

    role = (
        (profile.get("role") if profile else None)
        or (user_row.get("user_role") if user_row else None)
    )

    if role != "Donor":
        return None

    if not profile and user_row:
        insert_payload = {
            "user_id": user_row["id"],
            "organization_name": user_row.get("full_name") or "Hotel Donor",
            "address": "Jakarta",
        }
        try:
            created = supabase.table("user_profiles").insert(insert_payload).execute()
            profile = created.data[0] if created.data else None
        except Exception:
            insert_payload.update({
                "full_name": user_row.get("full_name"),
                "email": user_row.get("email"),
                "role": "Donor",
            })
            created = supabase.table("user_profiles").insert(insert_payload).execute()
            profile = created.data[0] if created.data else None

    return profile


def _enrich_listings(listings: list) -> list:
    if not listings:
        return []

    profiles_res = supabase.table("user_profiles").select("*").execute()
    users_res = supabase.table("users").select("*").execute()
    dispatches_res = supabase.table("delivery_dispatches").select("*").execute()

    users_by_id = {u["id"]: u for u in (users_res.data or []) if u.get("id")}
    profile_map = {}
    for p in (profiles_res.data or []):
        if p.get("id"):
            profile_map[p["id"]] = p
        if p.get("user_id"):
            profile_map[p["user_id"]] = p

    dispatch_by_listing = {
        d["listing_id"]: d
        for d in (dispatches_res.data or [])
        if d.get("listing_id")
    }

    enriched = []
    for item in listings:
        raw_donor = item.get("donor_id")
        donor = profile_map.get(raw_donor) or {}
        donor_uid = donor.get("user_id") or raw_donor
        donor_user = users_by_id.get(donor_uid) or {}

        raw_ben = item.get("matched_beneficiary_id")
        ben = profile_map.get(raw_ben) or {}
        ben_uid = ben.get("user_id") or raw_ben
        ben_user = users_by_id.get(ben_uid) or {}

        disp = dispatch_by_listing.get(item["id"]) or {}

        enriched.append({
            **item,
            "donor_profile_id": donor.get("id") or raw_donor,
            "donor_user_id": donor_uid,
            "hotel_name": (
                donor.get("organization_name")
                or donor.get("full_name")
                or donor_user.get("full_name")
                or "Hotel Donor"
            ),
            "hotel_address": donor.get("address") or "",
            "matched_beneficiary_user_id": ben_uid,
            "matched_beneficiary": (
                ben.get("organization_name")
                or ben.get("full_name")
                or ben_user.get("full_name")
            ),
            "beneficiary_address": ben.get("address") or "",
            "verification_pin": disp.get("verification_pin"),
            "distance_km": disp.get("estimated_distance_km"),
            "eta_mins": disp.get("estimated_duration_mins"),
            "delivery_status": disp.get("delivery_status"),
        })
    return enriched


def _fetch_filtered_listings(filter_id: Optional[str]) -> list:
    query = supabase.table("food_surplus_listings").select("*").order("created_at", desc=True)

    if filter_id:
        candidate_ids = {filter_id}
        # Resolve across both user_profiles.id and user_profiles.user_id regardless of role
        res_by_id = supabase.table("user_profiles").select("id, user_id").eq("id", filter_id).execute()
        res_by_uid = supabase.table("user_profiles").select("id, user_id").eq("user_id", filter_id).execute()
        for row in (res_by_id.data or []) + (res_by_uid.data or []):
            if row.get("id"):
                candidate_ids.add(row["id"])
            if row.get("user_id"):
                candidate_ids.add(row["user_id"])

        query = query.in_("donor_id", list(candidate_ids))

    response = query.execute()
    return _enrich_listings(response.data or [])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_food_listing(listing: FoodListingCreate):
    """Add a new food surplus listing and trigger LangGraph matching."""
    try:
        donor_profile = _resolve_donor_profile(listing.donor_id)
        if not donor_profile:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only registered Donor accounts can post food listings."
            )

        canonical_profile_id = donor_profile["id"]

        listing_data = {
            "donor_id": canonical_profile_id,
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

        enriched_created = _enrich_listings(response.data or [])

        return {
            "status": "success",
            "message": "Data makanan surplus berhasil ditambahkan!",
            "data": enriched_created,
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
def get_all_listings(
    donor_id: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
):
    """Fetch all surplus food listings, optionally filtered by user_profiles.id or users.id."""
    try:
        effective_filter = donor_id or user_id
        enriched = _fetch_filtered_listings(effective_filter)
        return {"status": "success", "data": enriched}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/donor/{donor_id}")
def get_listings_by_donor(donor_id: str):
    """Alias route to fetch listings by either user_profiles.id or users.id."""
    try:
        enriched = _fetch_filtered_listings(donor_id)
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