"""PostgreSQL-backed PocketPerks authentication and workspace role checks."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.config import Settings

async def authenticate_request(request: Request, settings: Settings) -> dict[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, access_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not access_token.strip():
        raise HTTPException(status_code=401, detail="Sign in to access PocketPerks.")
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Workspace sign-in is temporarily unavailable.")
    from app.auth_postgres import verify_access_token, workspace_user
    return await workspace_user(pool, verify_access_token(settings, access_token))


def allowed_roles(path: str, method: str) -> set[str]:
    if path == "/api/approvals/history/clear" and method == "POST":
        return {"admin"}
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
