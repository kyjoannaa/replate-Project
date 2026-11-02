from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from app.db import supabase

router = APIRouter(prefix="/api/users", tags=["Users"])

class UserCreate(BaseModel):
    email: str
    phone: Optional[str] = None
    full_name: str
    user_role: str # Harus: 'Donor', 'Beneficiary', atau 'Courier'

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate):
    """Register a new user."""
    try:
        user_data = user.dict()
        response = supabase.table("users").insert(user_data).execute()
        
        return {
            "status": "success",
            "message": f"User {user.full_name} berhasil didaftarkan!",
            "data": response.data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal register user: {str(e)}")