from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.db import supabase

security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Memvalidasi Supabase JWT Token dan mengambil profil role user dari tabel users."""
    token = credentials.credentials
    try:
        # 1. Verifikasi JWT token langsung ke Supabase Auth
        auth_response = supabase.auth.get_user(token)
        if not auth_response or not auth_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token tidak valid atau sudah kedaluwarsa."
            )

        auth_user = auth_response.user

        # 2. Ambil data role ('Donor', 'Beneficiary', 'Courier') dari tabel public.users
        res_db = supabase.table("users").select("*").eq("email", auth_user.email).execute()
        if not res_db.data:
            # Fallback jika dicari berdasarkan ID
            res_db = supabase.table("users").select("*").eq("id", auth_user.id).execute()

        if not res_db.data:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Akun terautentikasi, tetapi profil role belum terdaftar di tabel users."
            )

        return res_db.data[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Autentikasi gagal: {str(e)}"
        )


def require_role(*allowed_roles: str):
    """Dependency factory untuk membatasi endpoint hanya untuk role tertentu."""
    def role_checker(current_user: dict = Depends(get_current_user)) -> dict:
        user_role = current_user.get("user_role")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Akses ditolak. Endpoint ini hanya untuk role: {', '.join(allowed_roles)}."
            )
        return current_user

    return role_checker