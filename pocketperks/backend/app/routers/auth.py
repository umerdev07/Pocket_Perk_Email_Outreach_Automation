from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import auth_postgres

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=12, max_length=1024)


class EmailChangeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class EmailConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=256)


def _storage(request: Request):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL authentication is not configured.")
    return pool


def _set_refresh_cookie(response: Response, settings, token: str) -> None:
    response.set_cookie("pocketperks_refresh", token, max_age=settings.auth_refresh_token_days * 86400,
                        httponly=True, secure=settings.auth_cookie_secure,
                        samesite=settings.auth_cookie_samesite, path="/api/auth")


def _public_session(session: dict) -> dict:
    result = dict(session)
    result.pop("refresh_token", None)
    return result


@router.post("/sign-in")
async def sign_in(payload: Credentials, request: Request, response: Response):
    session = await auth_postgres.sign_in(_storage(request), request.app.state.settings,
                                          payload.email, payload.password)
    _set_refresh_cookie(response, request.app.state.settings, session["refresh_token"])
    return _public_session(session)


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("pocketperks_refresh", "")
    if not token:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    session = await auth_postgres.refresh_session(_storage(request), request.app.state.settings, token)
    _set_refresh_cookie(response, request.app.state.settings, session["refresh_token"])
    return _public_session(session)


@router.post("/sign-out", status_code=204)
async def sign_out(request: Request, response: Response):
    await auth_postgres.revoke_refresh_session(_storage(request), request.cookies.get("pocketperks_refresh", ""))
    response.delete_cookie("pocketperks_refresh", path="/api/auth", httponly=True,
                           secure=request.app.state.settings.auth_cookie_secure,
                           samesite=request.app.state.settings.auth_cookie_samesite)


@router.post("/password-reset")
async def password_reset(payload: ResetRequest, request: Request):
    await auth_postgres.request_password_reset(_storage(request), request.app.state.settings, payload.email)
    return {"accepted": True}


@router.post("/password-reset/confirm")
async def password_reset_confirm(payload: ResetConfirm, request: Request):
    await auth_postgres.reset_password(_storage(request), payload.token, payload.password)
    return {"updated": True}


@router.post("/email-change")
async def email_change(payload: EmailChangeRequest, request: Request):
    user = getattr(request.state, "workspace_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in again before changing your email.")
    await auth_postgres.request_email_change(_storage(request), request.app.state.settings,
                                             user["id"], payload.email)
    return {"accepted": True}


@router.post("/email-change/confirm")
async def email_change_confirm(payload: EmailConfirm, request: Request):
    return await auth_postgres.confirm_email_change(_storage(request), payload.token)
