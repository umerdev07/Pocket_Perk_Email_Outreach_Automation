"""Supabase Auth verification and PocketPerks workspace role checks."""

from __future__ import annotations

import hashlib
import time

from fastapi import HTTPException, Request
import httpx

from app.config import Settings

_CACHE_TTL_SECONDS = 20
_USER_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


async def authenticate_request(request: Request, settings: Settings) -> dict[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, access_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not access_token.strip():
        raise HTTPException(status_code=401, detail="Sign in to access PocketPerks.")
    if not settings.supabase_url or not settings.supabase_anon_key or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Workspace authentication is not configured on the server.")

    token_cache_key = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
    now = time.monotonic()
    for key in [key for key, (expiry, _) in _USER_CACHE.items() if expiry <= now]:
        _USER_CACHE.pop(key, None)
    cached = _USER_CACHE.get(token_cache_key)
    if cached and cached[0] > now:
        return cached[1]

    base_url = settings.supabase_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            auth_response = await client.get(
                f"{base_url}/auth/v1/user",
                headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {access_token}"},
            )
            if auth_response.status_code in (401, 403):
                raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
            if not auth_response.is_success:
                raise HTTPException(status_code=503, detail="Could not verify your workspace sign-in.")
            user = auth_response.json()
            user_id = user.get("id")
            if not isinstance(user_id, str):
                raise HTTPException(status_code=401, detail="The sign-in response was invalid.")

            role_response = await client.get(
                f"{base_url}/rest/v1/workspace_members",
                params={"select": "role", "user_id": f"eq.{user_id}", "limit": "1"},
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                },
            )
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=503, detail="Workspace sign-in is temporarily unavailable.") from None

    if not role_response.is_success:
        raise HTTPException(status_code=503, detail="Could not load your workspace access role.")
    rows = role_response.json()
    if not rows:
        raise HTTPException(status_code=403, detail="Your account has not been added to the PocketPerks workspace.")
    role = rows[0].get("role")
    if role not in {"admin", "operator", "reviewer"}:
        raise HTTPException(status_code=403, detail="Your PocketPerks workspace role is not valid.")
    workspace_user = {"id": user_id, "email": str(user.get("email", "")), "role": role}
    _USER_CACHE[token_cache_key] = (time.monotonic() + _CACHE_TTL_SECONDS, workspace_user)
    return workspace_user


def allowed_roles(path: str, method: str) -> set[str]:
    if path.startswith("/api/suppressions"):
        return {"admin"}
    if path == "/api/auth/me" or path.startswith("/api/dashboard/"):
        return {"admin", "operator", "reviewer"}
    if path == "/api/lead-replies" and method == "GET":
        return {"admin", "operator", "reviewer"}
    if path.startswith("/api/lead-replies/") and method == "PATCH":
        return {"admin", "operator", "reviewer"}
    if path.startswith("/api/lead-replies/") and method == "POST":
        return {"admin", "operator", "reviewer"}
    if method == "DELETE":
        return {"admin"}
    if path.startswith("/api/approvals"):
        return {"admin", "reviewer"}
    if method == "PATCH" and path.startswith("/api/emails/"):
        return {"admin", "operator", "reviewer"}
    if path.startswith("/api/emails/") and path.rsplit("/", 1)[-1] in {"approve", "reject", "recheck"}:
        return {"admin", "reviewer"}
    return {"admin", "operator"}
