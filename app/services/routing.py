import os
import math
import httpx

OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving"
MAPBOX_TRAFFIC_URL = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def get_real_route(
    pickup_lat: float,
    pickup_lon: float,
    dropoff_lat: float,
    dropoff_lon: float,
) -> dict:
    """
    Uses Mapbox driving-traffic if MAPBOX_ACCESS_TOKEN is configured in .env;
    otherwise uses keyless OSRM + OpenStreetMap street routing.
    """
    traffic_token = os.getenv("MAPBOX_ACCESS_TOKEN", "").strip()

    async with httpx.AsyncClient(timeout=8.0) as client:
        # Optional live-traffic provider when API key is added
        if traffic_token:
            try:
                url = f"{MAPBOX_TRAFFIC_URL}/{pickup_lon},{pickup_lat};{dropoff_lon},{dropoff_lat}"
                resp = await client.get(
                    url,
                    params={
                        "access_token": traffic_token,
                        "geometries": "geojson",
                        "overview": "full",
                    },
                )
                data = resp.json()
                if data.get("routes"):
                    route = data["routes"][0]
                    coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
                    return {
                        "distance_km": round(route["distance"] / 1000.0, 2),
                        "duration_mins": max(1, round(route["duration"] / 60.0)),
                        "route_polyline": coords,
                        "source": "mapbox-live-traffic",
                    }
            except Exception:
                pass

        # Keyless OSRM real street routing
        try:
            url = f"{OSRM_BASE_URL}/{pickup_lon},{pickup_lat};{dropoff_lon},{dropoff_lat}"
            resp = await client.get(
                url,
                params={"overview": "full", "geometries": "geojson"},
            )
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
                return {
                    "distance_km": round(route["distance"] / 1000.0, 2),
                    "duration_mins": max(1, round(route["duration"] / 60.0)),
                    "route_polyline": coords,
                    "source": "osrm-openstreetmap",
                }
        except Exception:
            pass

    # Fallback straight-line calculation if OSRM network request times out
    dist = round(haversine_km(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon), 2)
    mins = max(5, round((dist / 25.0) * 60))
    return {
        "distance_km": dist,
        "duration_mins": mins,
        "route_polyline": [[pickup_lat, pickup_lon], [dropoff_lat, dropoff_lon]],
        "source": "haversine-fallback",
    }