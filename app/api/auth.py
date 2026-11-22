from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import uuid
from app.db import supabase

router = APIRouter()


class SignupRequest(BaseModel):
    full_name: str
    email: str
    password: str
    role: str
    organization_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/signup")
async def signup(payload: SignupRequest):
    # Prevent public self-registration as Admin
    if payload.role == "Admin":
        raise HTTPException(status_code=403, detail="Admin accounts cannot be self-registered.")

    existing = supabase.table("user_profiles").select("*").eq("email", payload.email.lower()).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Email is already registered.")

    user_id = str(uuid.uuid4())
    row = {
        "id": user_id,
        "full_name": payload.full_name,
        "email": payload.email.lower(),
        "role": payload.role,
        "organization_name": payload.organization_name or payload.full_name,
        "address": payload.address or "Jakarta",
        "phone": payload.phone or "",
        "latitude": payload.latitude,
        "longitude": payload.longitude,
    }
    res = supabase.table("user_profiles").insert(row).execute()
    created = res.data[0] if res.data else row

    # Auto-create default beneficiary_constraints row so new Beneficiary is immediately eligible
    if payload.role == "Beneficiary":
        supabase.table("beneficiary_constraints").upsert({
            "beneficiary_id": user_id,
            "requires_halal": True,
            "requires_vegetarian": False,
            "max_capacity_servings": 50,
            "operating_hours": "08:00 - 20:00",
        }).execute()

    return {"access_token": f"token-{user_id}", "user": created}


@router.post("/login")
async def login(payload: LoginRequest):
    email_clean = payload.email.strip().lower()

    # Built-in Admin audit credential (not exposed on Signup form)
    if email_clean == "admin@replate.id" and payload.password == "admin123":
        return {
            "access_token": "token-admin-root",
            "user": {
                "id": "00000000-0000-0000-0000-000000000999",
                "full_name": "rePlate System Admin",
                "email": "admin@replate.id",
                "role": "Admin",
                "organization_name": "rePlate Operations Command",
                "address": "Universitas Indonesia, Depok",
            },
        }

    res = supabase.table("user_profiles").select("*").eq("email", email_clean).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    user = res.data[0]
    return {"access_token": f"token-{user['id']}", "user": user}