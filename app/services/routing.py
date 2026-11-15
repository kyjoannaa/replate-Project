#service module that queries the OpenStreetMap engine for real-time driving duration longitude points
import httpx
from app.config import settings

def calculate_delivery_eta(origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float) -> dict:
    """
    Calculates dynamic driving distance (km) and travel duration (hours) 
    between two geographic coordinates using OpenStreetMap OSRM API.
    """
    url = f"http://router.project-osrm.org/route/v1/driving/{origin_lng},{origin_lat};{dest_lng},{dest_lat}?overview=false"
    
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
            
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                distance_km = round(route["distance"] / 1000.0, 2)
                duration_hours = round(route["duration"] / 3600.0, 2)
                
                return {
                    "status": "success",
                    "distance_km": distance_km,
                    "duration_hours": duration_hours
                }
    except Exception as e:
        print(f"Routing API Error: {e}")
        
    # Default fallback buffer if routing API is unreachable
    return {
        "status": "fallback",
        "distance_km": 5.0,
        "duration_hours": 0.75
    }