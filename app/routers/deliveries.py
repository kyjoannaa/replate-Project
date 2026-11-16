import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from app.db import supabase
from app.dependencies.auth import get_current_user, require_role

router = APIRouter(prefix="/api/deliveries", tags=["Deliveries"])

class DeliveryDispatchCreate(BaseModel):
    negotiation_id: Optional[str] = None
    courier_id: Optional[str] = None
    pickup_address: str
    delivery_address: str
    estimated_distance_km: Optional[float] = None
    estimated_duration_mins: Optional[int] = None

class DeliveryStatusUpdate(BaseModel):
    delivery_status: str  # Contoh: "Assigned", "Picked Up", "In Transit", "Delivered"
    courier_id: Optional[str] = None

class CourierLocationUpdate(BaseModel):
    latitude: float
    longitude: float

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_delivery(
    delivery: DeliveryDispatchCreate,
    current_user: dict = Depends(require_role("Donor", "Courier"))
):
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

# --- ENDPOINT LIFECYCLE KURIR (GET & PATCH) ---

@router.get("/")
def get_all_deliveries(
    delivery_status: Optional[str] = None,
    courier_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Fetch all delivery dispatches, optionally filtered by delivery_status or courier_id."""
    try:
        query = supabase.table("delivery_dispatches").select("*")
        if delivery_status:
            query = query.eq("delivery_status", delivery_status)
        if courier_id:
            query = query.eq("courier_id", courier_id)
            
        response = query.execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{delivery_id}")
def get_delivery_by_id(
    delivery_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Fetch a specific delivery dispatch by ID."""
    try:
        response = supabase.table("delivery_dispatches").select("*").eq("id", delivery_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Delivery dispatch not found")
        return {"status": "success", "data": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{delivery_id}/status")
def update_delivery_status(
    delivery_id: str,
    payload: DeliveryStatusUpdate,
    current_user: dict = Depends(require_role("Courier"))
):
    """Update delivery_status and synchronize parent food listing status when delivered."""
    try:
        existing = supabase.table("delivery_dispatches").select("*").eq("id", delivery_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Delivery dispatch not found")

        dispatch = existing.data[0]
        update_data = {"delivery_status": payload.delivery_status}
        if payload.courier_id:
            update_data["courier_id"] = payload.courier_id

        res_update = supabase.table("delivery_dispatches") \
            .update(update_data) \
            .eq("id", delivery_id) \
            .execute()

        # Sinkronkan status food_surplus_listings menjadi "Delivered" via negotiation_id
        if payload.delivery_status.lower() == "delivered" and dispatch.get("negotiation_id"):
            try:
                neg = supabase.table("agent_negotiation_logs") \
                    .select("listing_id") \
                    .eq("id", dispatch["negotiation_id"]) \
                    .execute()
                if neg.data and neg.data[0].get("listing_id"):
                    supabase.table("food_surplus_listings") \
                        .update({"status": "Delivered"}) \
                        .eq("id", neg.data[0]["listing_id"]) \
                        .execute()
            except Exception as sync_err:
                print(f"[WARNING] Gagal sinkronisasi status listing ke Delivered: {sync_err}")

        return {
            "status": "success",
            "message": f"Status pengiriman berhasil diperbarui menjadi '{payload.delivery_status}'.",
            "data": res_update.data[0] if res_update.data else update_data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT PETA & RUTE REAL-TIME (OSRM POLYLINE & LIVE GPS) ---

@router.get("/{delivery_id}/route")
def get_delivery_map_route(
    delivery_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Return pickup/dropoff coordinates, OSRM road polyline, and live courier GPS."""
    try:
        res_disp = supabase.table("delivery_dispatches").select("*").eq("id", delivery_id).execute()
        if not res_disp.data:
            raise HTTPException(status_code=404, detail="Delivery dispatch not found")
        dispatch = res_disp.data[0]

        # Koordinat default (Jakarta Pusat) jika profil belum punya lat/lon
        pickup_coords = {"latitude": -6.1754, "longitude": 106.8272, "label": dispatch.get("pickup_address", "Pickup Point")}
        dropoff_coords = {"latitude": -6.2088, "longitude": 106.8456, "label": dispatch.get("delivery_address", "Dropoff Point")}

        # Telusuri koordinat asli Donor & Beneficiary dari user_profiles
        if dispatch.get("negotiation_id"):
            res_neg = supabase.table("agent_negotiation_logs") \
                .select("listing_id, beneficiary_id") \
                .eq("id", dispatch["negotiation_id"]) \
                .execute()

            if res_neg.data:
                neg = res_neg.data[0]
                beneficiary_id = neg.get("beneficiary_id")
                listing_id = neg.get("listing_id")

                donor_id = None
                if listing_id:
                    res_list = supabase.table("food_surplus_listings").select("donor_id").eq("id", listing_id).execute()
                    if res_list.data:
                        donor_id = res_list.data[0].get("donor_id")

                res_profs = supabase.table("user_profiles").select("*").execute()
                for p in (res_profs.data or []):
                    uid = p.get("user_id") or p.get("id")
                    if donor_id and uid == donor_id and p.get("latitude") and p.get("longitude"):
                        pickup_coords = {
                            "latitude": float(p["latitude"]),
                            "longitude": float(p["longitude"]),
                            "label": p.get("organization_name") or dispatch.get("pickup_address")
                        }
                    if beneficiary_id and uid == beneficiary_id and p.get("latitude") and p.get("longitude"):
                        dropoff_coords = {
                            "latitude": float(p["latitude"]),
                            "longitude": float(p["longitude"]),
                            "label": p.get("organization_name") or dispatch.get("delivery_address")
                        }

        # Panggil OSRM untuk mendapatkan garis rute jalan (GeoJSON)
        osrm_url = (
            f"http://router.project-osrm.org/route/v1/driving/"
            f"{pickup_coords['longitude']},{pickup_coords['latitude']};"
            f"{dropoff_coords['longitude']},{dropoff_coords['latitude']}"
            f"?overview=full&geometries=geojson"
        )

        route_polyline = []
        distance_km = dispatch.get("estimated_distance_km")
        duration_mins = dispatch.get("estimated_duration_mins")

        try:
            osrm_res = requests.get(osrm_url, timeout=8).json()
            if osrm_res.get("code") == "Ok" and osrm_res.get("routes"):
                best_route = osrm_res["routes"][0]
                distance_km = round(best_route["distance"] / 1000, 2)
                duration_mins = int(round(best_route["duration"] / 60))

                # OSRM mengembalikan [lon, lat] -> ubah ke {latitude, longitude} untuk FE Map
                raw_coords = best_route["geometry"]["coordinates"]
                route_polyline = [
                    {"latitude": coord[1], "longitude": coord[0]}
                    for coord in raw_coords
                ]
        except Exception as osrm_err:
            print(f"[WARNING] OSRM polyline fallback: {osrm_err}")
            route_polyline = [
                {"latitude": pickup_coords["latitude"], "longitude": pickup_coords["longitude"]},
                {"latitude": dropoff_coords["latitude"], "longitude": dropoff_coords["longitude"]}
            ]

        courier_location = None
        if dispatch.get("courier_latitude") and dispatch.get("courier_longitude"):
            courier_location = {
                "latitude": float(dispatch["courier_latitude"]),
                "longitude": float(dispatch["courier_longitude"])
            }

        return {
            "status": "success",
            "delivery_id": delivery_id,
            "delivery_status": dispatch.get("delivery_status"),
            "pickup": pickup_coords,
            "dropoff": dropoff_coords,
            "courier_location": courier_location,
            "metrics": {
                "distance_km": distance_km,
                "estimated_duration_mins": duration_mins
            },
            "route_polyline": route_polyline
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{delivery_id}/location")
def update_courier_location(
    delivery_id: str,
    location: CourierLocationUpdate,
    current_user: dict = Depends(require_role("Courier"))
):
    """Update live GPS coordinates of the courier for real-time map tracking."""
    try:
        res = supabase.table("delivery_dispatches").update({
            "courier_latitude": location.latitude,
            "courier_longitude": location.longitude
        }).eq("id", delivery_id).execute()

        if not res.data:
            raise HTTPException(status_code=404, detail="Delivery dispatch not found")

        return {
            "status": "success",
            "message": "Posisi GPS kurir diperbarui.",
            "courier_location": {
                "latitude": location.latitude,
                "longitude": location.longitude
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))