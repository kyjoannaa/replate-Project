from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Literal, Optional
from app.db import supabase
from app.dependencies.auth import get_current_user

router = APIRouter(prefix="/api/users", tags=["Users"])

ALLOWED_ROLES = ("Donor", "Beneficiary", "Courier")


class UserCreate(BaseModel):
    email: str
    phone: Optional[str] = None
    full_name: str
    user_role: str  # Harus: 'Donor', 'Beneficiary', atau 'Courier'


class UserSignUp(BaseModel):
    email: str
    password: str
    full_name: str
    phone: Optional[str] = None
    user_role: Literal["Donor", "Beneficiary", "Courier"]
    organization_name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class UserLogin(BaseModel):
    email: str
    password: str


class UserProfileUpdate(BaseModel):
    organization_name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# --- AUTHENTICATION ENDPOINTS (SIGN UP & LOGIN) ---

@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup_user(payload: UserSignUp):
    """Register user in Supabase Auth, save role to public.users, and initialize user_profiles."""
    try:
        # 1. Daftarkan akun ke Supabase Auth
        auth_res = supabase.auth.sign_up({
            "email": payload.email,
            "password": payload.password,
            "options": {
                "data": {
                    "full_name": payload.full_name,
                    "user_role": payload.user_role
                }
            }
        })

        if not auth_res or not auth_res.user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gagal membuat akun di Supabase Auth."
            )

        auth_user_id = auth_res.user.id

        # 2. Simpan data akun & role ke tabel public.users
        user_record = {
            "id": auth_user_id,
            "email": payload.email,
            "phone": payload.phone,
            "full_name": payload.full_name,
            "user_role": payload.user_role
        }

        try:
            res_db = supabase.table("users").insert(user_record).execute()
        except Exception:
            # Fallback jika kolom id di tabel users di-generate otomatis oleh DB
            user_record.pop("id", None)
            res_db = supabase.table("users").insert(user_record).execute()

        created_user = res_db.data[0] if res_db.data else user_record
        db_user_id = created_user.get("id", auth_user_id)

        # 3. Buat row awal di user_profiles jika info organisasi/lokasi diisi
        created_profile = None
        if any([payload.organization_name, payload.address, payload.latitude, payload.longitude]):
            try:
                profile_payload = {
                    "user_id": db_user_id,
                    "organization_name": payload.organization_name or payload.full_name,
                    "address": payload.address,
                    "latitude": payload.latitude,
                    "longitude": payload.longitude
                }
                res_prof = supabase.table("user_profiles").insert(profile_payload).execute()
                created_profile = res_prof.data[0] if res_prof.data else profile_payload
            except Exception as prof_err:
                print(f"[WARNING] Gagal inisialisasi user_profiles saat signup: {prof_err}")

        access_token = auth_res.session.access_token if auth_res.session else None

        return {
            "status": "success",
            "message": f"Registrasi berhasil sebagai {payload.user_role}!",
            "access_token": access_token,
            "token_type": "bearer" if access_token else None,
            "data": {
                "user": created_user,
                "profile": created_profile
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal sign up: {str(e)}")


@router.post("/login")
def login_user(credentials: UserLogin):
    """Authenticate user via Supabase Auth and return JWT access_token, role, and profile."""
    try:
        auth_res = supabase.auth.sign_in_with_password({
            "email": credentials.email,
            "password": credentials.password
        })

        if not auth_res or not auth_res.session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email atau password salah."
            )

        # Ambil data akun & role dari public.users
        res_user = supabase.table("users").select("*").eq("email", credentials.email).execute()
        user_account = res_user.data[0] if res_user.data else None

        # Ambil data profil organisasi dari user_profiles jika ada
        user_profile = None
        if user_account and user_account.get("id"):
            res_prof = supabase.table("user_profiles").select("*").eq("user_id", user_account["id"]).execute()
            if not res_prof.data:
                res_prof = supabase.table("user_profiles").select("*").eq("id", user_account["id"]).execute()
            user_profile = res_prof.data[0] if res_prof.data else None

        return {
            "status": "success",
            "message": "Login berhasil!",
            "access_token": auth_res.session.access_token,
            "token_type": "bearer",
            "role": user_account.get("user_role") if user_account else None,
            "user": user_account,
            "profile": user_profile
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Login gagal: {str(e)}"
        )


# --- ENDPOINT BAWAAN & CRUD PROFIL ---

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate):
    """Register a new user directly in public.users."""
    try:
        if user.user_role not in ALLOWED_ROLES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"user_role harus salah satu dari: {', '.join(ALLOWED_ROLES)}"
            )
        user_data = user.dict()
        response = supabase.table("users").insert(user_data).execute()

        return {
            "status": "success",
            "message": f"User {user.full_name} berhasil didaftarkan!",
            "data": response.data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal register user: {str(e)}")


@router.get("/")
def get_all_users(
    user_role: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Fetch all users merged with their organization profile & coordinates."""
    try:
        query = supabase.table("users").select("*")
        if user_role:
            query = query.eq("user_role", user_role)
        res_users = query.execute()

        res_profiles = supabase.table("user_profiles").select("*").execute()
        profiles_map = {}
        for p in (res_profiles.data or []):
            if p.get("user_id"):
                profiles_map[p["user_id"]] = p
            if p.get("id"):
                profiles_map[p["id"]] = p

        enriched_users = []
        for u in (res_users.data or []):
            user_copy = u.copy()
            user_copy["profile"] = profiles_map.get(u.get("id"))
            enriched_users.append(user_copy)

        return {"status": "success", "data": enriched_users}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{user_id}")
def get_user_by_id(
    user_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Fetch a single user account and their organization profile by user_id."""
    try:
        res_user = supabase.table("users").select("*").eq("id", user_id).execute()
        res_prof = supabase.table("user_profiles").select("*").eq("user_id", user_id).execute()
        if not res_prof.data:
            res_prof = supabase.table("user_profiles").select("*").eq("id", user_id).execute()

        if not res_user.data and not res_prof.data:
            raise HTTPException(status_code=404, detail="User not found")

        return {
            "status": "success",
            "data": {
                "account": res_user.data[0] if res_user.data else None,
                "profile": res_prof.data[0] if res_prof.data else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{user_id}/profile")
def update_or_create_user_profile(
    user_id: str,
    payload: UserProfileUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update organization profile & GPS coordinates (or create if not yet present)."""
    try:
        update_data = {k: v for k, v in payload.dict().items() if v is not None}
        if not update_data:
            raise HTTPException(status_code=400, detail="Tidak ada data profil yang dikirim.")

        existing = supabase.table("user_profiles").select("*").eq("user_id", user_id).execute()
        if not existing.data:
            existing = supabase.table("user_profiles").select("*").eq("id", user_id).execute()

        if existing.data:
            target_col = "user_id" if existing.data[0].get("user_id") == user_id else "id"
            res = supabase.table("user_profiles").update(update_data).eq(target_col, user_id).execute()
        else:
            insert_payload = {"user_id": user_id, **update_data}
            res = supabase.table("user_profiles").insert(insert_payload).execute()

        return {
            "status": "success",
            "message": "Profil organisasi berhasil disimpan!",
            "data": res.data[0] if res.data else update_data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))