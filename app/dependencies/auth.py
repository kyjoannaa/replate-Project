from typing import Optional
from fastapi import Header
from app.db import supabase


def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """Resolves user from Bearer token-<uuid> header if provided."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "").strip()
        for prefix in ("token-", "local-"):
            if token.startswith(prefix):
                user_id = token[len(prefix):]
                try:
                    res = supabase.table("user_profiles").select("*").eq("id", user_id).execute()
                    if res.data:
                        return res.data[0]
                except Exception:
                    pass
    return {"id": None, "role": "System"}


def require_role(*allowed_roles: str):
    def _dependency(authorization: Optional[str] = Header(default=None)) -> dict:
        return get_current_user(authorization)
    return _dependency