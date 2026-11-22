from fastapi import APIRouter
from app.db import supabase
from app.agents.nodes import run_matching_workflow

router = APIRouter()


@router.get("/overview")
async def get_admin_overview():
    profiles = (supabase.table("user_profiles").select("*").execute()).data or []
    constraints = (supabase.table("beneficiary_constraints").select("*").execute()).data or []
    listings = (
        supabase.table("food_surplus_listings")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    ).data or []
    dispatches = (
        supabase.table("delivery_dispatches")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    ).data or []

    profile_map = {p["id"]: p for p in profiles}
    constraint_map = {c["beneficiary_id"]: c for c in constraints}
    dispatch_by_listing = {d["listing_id"]: d for d in dispatches if d.get("listing_id")}

    hotels = [p for p in profiles if p.get("role") == "Donor"]
    beneficiaries = []
    for p in profiles:
        if p.get("role") == "Beneficiary":
            beneficiaries.append({
                **p,
                "constraints": constraint_map.get(p["id"], {}),
            })

    enriched_listings = []
    for item in listings:
        donor = profile_map.get(item.get("donor_id"), {})
        ben = profile_map.get(item.get("matched_beneficiary_id"), {})
        disp = dispatch_by_listing.get(item["id"], {})
        enriched_listings.append({
            **item,
            "hotel_name": donor.get("organization_name") or donor.get("full_name") or "Unknown Hotel",
            "hotel_address": donor.get("address"),
            "matched_beneficiary": ben.get("organization_name") or ben.get("full_name"),
            "beneficiary_address": ben.get("address"),
            "verification_pin": disp.get("verification_pin"),
            "distance_km": disp.get("estimated_distance_km"),
            "eta_mins": disp.get("estimated_duration_mins"),
            "delivery_status": disp.get("delivery_status"),
        })

    return {
        "hotels": hotels,
        "beneficiaries": beneficiaries,
        "listings": enriched_listings,
        "dispatches": dispatches,
    }


@router.post("/rematch/{listing_id}")
async def rematch_listing(listing_id: str):
    result = await run_matching_workflow(listing_id)
    return result