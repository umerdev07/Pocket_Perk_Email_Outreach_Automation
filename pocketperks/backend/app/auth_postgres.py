"""Local PostgreSQL authentication with short-lived signed access tokens."""

from __future__ import annotations

import asyncio
import binascii
import base64
import hashlib
import hmac
import json
import secrets
import smtplib
import ssl
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import quote

import asyncpg
from fastapi import HTTPException

from app.config import Settings

ACCESS_TOKEN_SECONDS = 15 * 60
RESET_TOKEN_MINUTES = 30
EMAIL_CHANGE_TOKEN_HOURS = 24
LOGIN_WINDOW_MINUTES = 15
LOGIN_FAILURE_LIMIT = 8


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret(settings: Settings) -> bytes:
    value = settings.auth_jwt_secret.encode("utf-8")
    if len(value) < 32:
        raise HTTPException(status_code=503, detail="Workspace sign-in is not configured on the server.")
    return value


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$%s$%s" % (_b64(salt), _b64(digest))


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(_unb64(expected)))
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError, MemoryError):
        return False


def hash_password(password: str) -> str:
    """Hash passwords for the one-time administrator bootstrap utility."""
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    return _password_hash(password)


def _access_token(settings: Settings, user_id: str, email: str, session_id: str) -> tuple[str, int]:
    now = int(datetime.now(UTC).timestamp())
    expiry = now + settings.auth_access_token_minutes * 60
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"sub": user_id, "sid": session_id, "email": email, "iat": now, "exp": expiry,
                              "iss": "pocketperks"}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(_secret(settings), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}", expiry


def verify_access_token(settings: Settings, token: str) -> dict:
    try:
        header_part, payload_part, signature_part = token.split(".", 2)
        header = json.loads(_unb64(header_part))
        payload = json.loads(_unb64(payload_part))
        expected = hmac.new(_secret(settings), f"{header_part}.{payload_part}".encode("ascii"), hashlib.sha256).digest()
        if header != {"alg": "HS256", "typ": "JWT"} or not hmac.compare_digest(expected, _unb64(signature_part)):
            raise ValueError
        if payload.get("iss") != "pocketperks" or int(payload.get("exp", 0)) <= int(datetime.now(UTC).timestamp()):
            raise ValueError
        user_id = payload.get("sub")
        session_id = payload.get("sid")
        if not isinstance(user_id, str) or not isinstance(session_id, str):
            raise ValueError
        uuid.UUID(user_id)
        uuid.UUID(session_id)
        return {"id": user_id, "session_id": session_id}
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.") from None


async def _user_for_email(pool, email: str):
    return await pool.fetchrow("""select u.id,u.email,u.password_hash,u.is_active,m.role
        from public.users u join public.workspace_members m on m.user_id=u.id
        where u.email=$1""", email)


async def _issue_session(pool, settings: Settings, user) -> dict:
    _secret(settings)
    now = datetime.now(UTC)
    refresh = secrets.token_urlsafe(48)
    expires = now + timedelta(days=settings.auth_refresh_token_days)
    session_id = await pool.fetchval("""insert into public.refresh_sessions(user_id,token_hash,expires_at,last_used_at)
        values($1,$2,$3,$4) returning id""", user["id"], hashlib.sha256(refresh.encode()).hexdigest(), expires, now)
    access, access_expires = _access_token(settings, str(user["id"]), user["email"], str(session_id))
    return {"access_token": access, "refresh_token": refresh,
            "expires_in": settings.auth_access_token_minutes * 60, "expires_at": access_expires,
            "user": {"id": str(user["id"]), "email": user["email"]}}


def _email_digest(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


async def sign_in(pool, settings: Settings, email: str, password: str) -> dict:
    normalized = email.strip().lower()
    digest = _email_digest(normalized)
    async with pool.acquire() as conn:
        attempt = await conn.fetchrow("select failed_count,window_started_at,locked_until from public.auth_login_attempts where email_hash=$1", digest)
        now = datetime.now(UTC)
        if attempt and attempt["locked_until"] and attempt["locked_until"] > now:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Wait a little and try again.")
        user = await _user_for_email(conn, normalized)
        if not user or not user["is_active"] or not _password_matches(password, user["password_hash"]):
            count = (attempt["failed_count"] + 1 if attempt and now - attempt["window_started_at"] < timedelta(minutes=LOGIN_WINDOW_MINUTES) else 1)
            locked_until = now + timedelta(minutes=LOGIN_WINDOW_MINUTES) if count >= LOGIN_FAILURE_LIMIT else None
            await conn.execute("""insert into public.auth_login_attempts(email_hash,failed_count,window_started_at,locked_until,updated_at)
                values($1,$2,$3,$4,$3) on conflict(email_hash) do update set failed_count=$2,
                window_started_at=$3,locked_until=$4,updated_at=$3""", digest, count, now, locked_until)
            raise HTTPException(status_code=401, detail="That email and password do not match. Please try again.")
        await conn.execute("delete from public.auth_login_attempts where email_hash=$1", digest)
        await conn.execute("update public.users set last_login_at=$2 where id=$1", user["id"], now)
        return await _issue_session(conn, settings, user)


async def refresh_session(pool, settings: Settings, refresh_token: str) -> dict:
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""select s.id,s.user_id from public.refresh_sessions s
                where s.token_hash=$1 and s.revoked_at is null and s.expires_at>now() for update""", token_hash)
            if row is None:
                raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
            user = await conn.fetchrow("""select u.id,u.email,u.is_active,m.role from public.users u
                join public.workspace_members m on m.user_id=u.id where u.id=$1""", row["user_id"])
            if user is None or not user["is_active"]:
                await conn.execute("update public.refresh_sessions set revoked_at=now() where id=$1", row["id"])
                raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
            await conn.execute("update public.refresh_sessions set revoked_at=now(),last_used_at=now() where id=$1", row["id"])
            return await _issue_session(conn, settings, user)


async def revoke_refresh_session(pool, refresh_token: str) -> None:
    if not refresh_token:
        return
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    await pool.execute("update public.refresh_sessions set revoked_at=now() where token_hash=$1 and revoked_at is null", token_hash)


async def workspace_user(pool, token_payload: dict) -> dict:
    try:
        user_id = token_payload["id"]
        session_id = token_payload["session_id"]
        row = await pool.fetchrow("""select u.id,u.email,u.is_active,m.role from public.users u
            join public.workspace_members m on m.user_id=u.id join public.refresh_sessions s on s.user_id=u.id
            where u.id=$1::uuid and s.id=$2::uuid and s.revoked_at is null and s.expires_at>now()""",
            user_id, session_id)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.") from None
    except (asyncpg.PostgresError, OSError, TimeoutError):
        raise HTTPException(status_code=503, detail="Workspace sign-in is temporarily unavailable.") from None
    if row is None or not row["is_active"] or row["role"] not in {"admin", "operator", "reviewer"}:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return {"id": str(row["id"]), "email": row["email"], "role": row["role"]}


def _smtp_ready(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from_email)


def _send_message(settings: Settings, to_email: str, subject: str, body: str) -> None:
    if not _smtp_ready(settings):
        raise HTTPException(status_code=503, detail="Email delivery is not configured on the server.")
    message = EmailMessage()
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_use_tls:
            server.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


async def _send_mail(settings: Settings, to_email: str, subject: str, body: str) -> None:
    try:
        await asyncio.to_thread(_send_message, settings, to_email, subject, body)
    except HTTPException:
        raise
    except (OSError, smtplib.SMTPException):
        raise HTTPException(status_code=503, detail="Email delivery is temporarily unavailable.") from None


async def request_password_reset(pool, settings: Settings, email: str) -> None:
    normalized = email.strip().lower()
    user = await _user_for_email(pool, normalized)
    if user is None:
        return
    await pool.execute("update public.password_reset_tokens set used_at=now() where user_id=$1 and used_at is null", user["id"])
    token = secrets.token_urlsafe(40)
    expires = datetime.now(UTC) + timedelta(minutes=RESET_TOKEN_MINUTES)
    await pool.execute("""insert into public.password_reset_tokens(user_id,token_hash,expires_at)
        values($1,$2,$3)""", user["id"], hashlib.sha256(token.encode()).hexdigest(), expires)
    origin = settings.password_reset_origin.rstrip("/")
    if not origin:
        raise HTTPException(status_code=503, detail="Password reset delivery is not configured on the server.")
    link = f"{origin}/auth/reset-password?token={quote(token)}"
    await _send_mail(settings, normalized, "Reset your PocketPerks password",
                     f"Use this link within {RESET_TOKEN_MINUTES} minutes to set a new password:\n\n{link}\n\nIf you did not request this, you can ignore this email.")


async def reset_password(pool, token: str, password: str) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""select id,user_id from public.password_reset_tokens
                where token_hash=$1 and used_at is null and expires_at>now() for update""", token_hash)
            if row is None:
                raise HTTPException(status_code=400, detail="This password reset link has expired. Request a new one.")
            await conn.execute("update public.users set password_hash=$2,updated_at=now() where id=$1", row["user_id"], _password_hash(password))
            await conn.execute("update public.password_reset_tokens set used_at=now() where id=$1", row["id"])
            await conn.execute("update public.refresh_sessions set revoked_at=now() where user_id=$1 and revoked_at is null", row["user_id"])


async def request_email_change(pool, settings: Settings, user_id: str, new_email: str) -> None:
    new_email = new_email.strip().lower()
    async with pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("select id,email from public.users where id=$1::uuid and is_active for update", user_id)
            if user is None:
                raise HTTPException(status_code=401, detail="Please sign in again before changing your email.")
            if user["email"] == new_email:
                raise HTTPException(status_code=422, detail="Enter a different email address.")
            if await conn.fetchval("select exists(select 1 from public.users where email=$1)", new_email):
                raise HTTPException(status_code=409, detail="That email address is already in use.")
            await conn.execute("delete from public.email_change_tokens where user_id=$1", user["id"])
            tokens = []
            expires = datetime.now(UTC) + timedelta(hours=EMAIL_CHANGE_TOKEN_HOURS)
            for purpose, target in (("current", user["email"]), ("new", new_email)):
                token = secrets.token_urlsafe(40)
                tokens.append((purpose, target, token))
                await conn.execute("""insert into public.email_change_tokens(user_id,old_email,new_email,token_hash,purpose,expires_at)
                    values($1,$2,$3,$4,$5,$6)""", user["id"], user["email"], new_email,
                    hashlib.sha256(token.encode()).hexdigest(), purpose, expires)
    origin = settings.password_reset_origin.rstrip("/")
    if not origin:
        raise HTTPException(status_code=503, detail="Email change delivery is not configured on the server.")
    for purpose, target, token in tokens:
        link = f"{origin}/auth/confirm-email?token={quote(token)}"
        await _send_mail(settings, target, "Confirm your PocketPerks login email change",
                         f"Confirm the {purpose} address for your login email change within {EMAIL_CHANGE_TOKEN_HOURS} hours:\n\n{link}\n\nIf you did not request this, contact your workspace admin.")


async def confirm_email_change(pool, token: str) -> dict:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""select id,user_id,new_email,purpose from public.email_change_tokens
                where token_hash=$1 and used_at is null and expires_at>now() for update""", token_hash)
            if row is None:
                raise HTTPException(status_code=400, detail="This email confirmation link has expired or was already used.")
            await conn.execute("update public.email_change_tokens set used_at=now() where id=$1", row["id"])
            remaining = await conn.fetchval("""select count(*) from public.email_change_tokens where user_id=$1
                and new_email=$2 and expires_at>now() and used_at is null""", row["user_id"], row["new_email"])
            if remaining:
                return {"confirmed": True, "complete": False}
            if await conn.fetchval("select exists(select 1 from public.users where email=$1 and id<>$2)", row["new_email"], row["user_id"]):
                raise HTTPException(status_code=409, detail="That email address is already in use.")
            await conn.execute("update public.users set email=$2,updated_at=now() where id=$1", row["user_id"], row["new_email"])
            await conn.execute("delete from public.email_change_tokens where user_id=$1", row["user_id"])
            await conn.execute("update public.refresh_sessions set revoked_at=now() where user_id=$1 and revoked_at is null", row["user_id"])
            return {"confirmed": True, "complete": True}
