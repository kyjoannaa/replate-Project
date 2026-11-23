from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from typing import Optional
from app.db import supabase
from app.dependencies.auth import get_current_user

router = APIRouter(prefix="/api/users", tags=["Users"])
auth_router = APIRouter(prefix="/api/auth", tags=["Auth"])

ALLOWED_ROLES = ("Donor", "Beneficiary", "Courier")


def normalize_role(raw_role: str) -> str:
    """Normalize frontend role values/labels into 'Donor', 'Beneficiary', or 'Courier'."""
    val = (raw_role or "").strip().lower()
    if "donor" in val or "hotel" in val or "fnb" in val:
        return "Donor"
    if "beneficiary" in val or "yayasan" in val or "panti" in val or "shelter" in val:
        return "Beneficiary"
    if "courier" in val or "kurir" in val or "driver" in val:
        return "Courier"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"user_role tidak valid: '{raw_role}'. Harus salah satu dari: {', '.join(ALLOWED_ROLES)}"
    )


def _ensure_user_profile(
    db_user_id: str,
    full_name: str,
    email: str,
    role: str,
    org_name: Optional[str] = None,
    address: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Optional[dict]:
    """Fetches existing user_profiles row or creates one linked via user_id."""
    res_prof = supabase.table("user_profiles").select("*").eq("user_id", db_user_id).execute()
    if not res_prof.data:
        res_prof = supabase.table("user_profiles").select("*").eq("id", db_user_id).execute()
    if res_prof.data:
        return res_prof.data[0]

    base_payload = {
        "user_id": db_user_id,
        "organization_name": org_name or full_name,
        "address": address or "Jakarta",
        "latitude": latitude,
        "longitude": longitude,
    }
    try:
        created = supabase.table("user_profiles").insert(base_payload).execute()
        return created.data[0] if created.data else base_payload
    except Exception:
        extended_payload = {
            **base_payload,
            "full_name": full_name,
            "email": email,
            "role": role,
        }
        try:
            created = supabase.table("user_profiles").insert(extended_payload).execute()
            return created.data[0] if created.data else extended_payload
        except Exception as prof_err:
            print(f"[WARNING] Gagal inisialisasi user_profiles: {prof_err}")
            return None


class UserCreate(BaseModel):
    email: str
    phone: Optional[str] = None
    full_name: str
    user_role: str


class UserSignUp(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    password: str
    full_name: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    user_role: Optional[str] = None
    role: Optional[str] = None
    organization_name: Optional[str] = None
    organization: Optional[str] = None
    org_name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class UserLogin(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    password: str


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    organization_name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# --- AUTHENTICATION ENDPOINTS (/api/users & /api/auth) ---

@router.post("/signup", status_code=status.HTTP_201_CREATED)
@auth_router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup_user(payload: UserSignUp):
    """Register user in Supabase Auth, save role to public.users, and initialize user_profiles."""
    try:
        resolved_name = (payload.full_name or payload.name or payload.email.split("@")[0]).strip()
        resolved_role = payload.user_role or payload.role or ""
        resolved_org = payload.organization_name or payload.organization or payload.org_name
        canonical_role = normalize_role(resolved_role)

        # 1. Daftarkan akun ke Supabase Auth
        auth_res = supabase.auth.sign_up({
            "email": payload.email,
            "password": payload.password,
            "options": {
                "data": {
                    "full_name": resolved_name,
                    "user_role": canonical_role
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
            "full_name": resolved_name,
            "user_role": canonical_role
        }

        try:
            res_db = supabase.table("users").insert(user_record).execute()
        except Exception:
            user_record.pop("id", None)
            res_db = supabase.table("users").insert(user_record).execute()

        created_user = res_db.data[0] if res_db.data else user_record
        db_user_id = created_user.get("id", auth_user_id)

        # 3. Selalu pastikan row di user_profiles dibuat agar profile_id selalu tersedia
        created_profile = _ensure_user_profile(
            db_user_id=db_user_id,
            full_name=resolved_name,
            email=payload.email,
            role=canonical_role,
            org_name=resolved_org,
            address=payload.address,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )

        # Auto-create default beneficiary_constraints jika role Beneficiary
        if canonical_role == "Beneficiary" and created_profile and created_profile.get("id"):
            try:
                supabase.table("beneficiary_constraints").upsert({
                    "beneficiary_id": created_profile["id"],
                    "requires_halal": True,
                    "requires_vegetarian": False,
                    "max_capacity_servings": 50,
                }).execute()
            except Exception:
                pass

        access_token = auth_res.session.access_token if auth_res.session else None
        profile_id = created_profile.get("id") if created_profile else db_user_id

        return {
            "status": "success",
            "message": f"Registrasi berhasil sebagai {canonical_role}!",
            "access_token": access_token,
            "token_type": "bearer" if access_token else None,
            "role": canonical_role,
            "profile_id": profile_id,
            "user": created_user,
            "profile": created_profile,
            "data": {
                "user": created_user,
                "profile": created_profile,
                "profile_id": profile_id,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal sign up: {str(e)}")


@router.post("/login")
@auth_router.post("/login")
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

        res_user = supabase.table("users").select("*").eq("email", credentials.email).execute()
        user_account = res_user.data[0] if res_user.data else None

        user_profile = None
        if user_account and user_account.get("id"):
            user_profile = _ensure_user_profile(
                db_user_id=user_account["id"],
                full_name=user_account.get("full_name") or credentials.email.split("@")[0],
                email=credentials.email,
                role=user_account.get("user_role") or "Donor",
            )

        profile_id = user_profile.get("id") if user_profile else (user_account.get("id") if user_account else None)

        return {
            "status": "success",
            "message": "Login berhasil!",
            "access_token": auth_res.session.access_token,
            "token_type": "bearer",
            "role": user_account.get("user_role") if user_account else None,
            "profile_id": profile_id,
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
        canonical_role = normalize_role(user.user_role)
        user_data = user.dict()
        user_data["user_role"] = canonical_role
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
            query = query.eq("user_role", normalize_role(user_role))
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
            prof = profiles_map.get(u.get("id"))
            user_copy["profile"] = prof
            user_copy["profile_id"] = prof.get("id") if prof else u.get("id")
            enriched_users.append(user_copy)

        return {"status": "success", "data": enriched_users}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{user_id}")
def get_user_by_id(
    user_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Fetch a single user account and their organization profile by user_id or profile_id."""
    try:
        res_user = supabase.table("users").select("*").eq("id", user_id).execute()
        res_prof = supabase.table("user_profiles").select("*").eq("user_id", user_id).execute()
        if not res_prof.data:
            res_prof = supabase.table("user_profiles").select("*").eq("id", user_id).execute()

        if not res_user.data and not res_prof.data:
            raise HTTPException(status_code=404, detail="User not found")

        prof = res_prof.data[0] if res_prof.data else None
        acct = res_user.data[0] if res_user.data else None

        return {
            "status": "success",
            "data": {
                "account": acct,
                "profile": prof,
                "profile_id": prof.get("id") if prof else (acct.get("id") if acct else None),
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