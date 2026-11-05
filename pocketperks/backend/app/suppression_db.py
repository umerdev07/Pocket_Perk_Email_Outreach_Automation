"""Service-role access to the workspace-wide email suppression list."""

import re

import httpx
from fastapi import HTTPException

from app.config import Settings

_EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.I)
_CHUNK_SIZE = 200


def normalize_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    return email if _EMAIL_RE.fullmatch(email) else None


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for email suppression.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def get_suppressed_emails(settings: Settings, emails: set[str] | list[str]) -> set[str]:
    """Fail closed: a missing migration or DB error must not permit a send."""
    normalized = sorted({email for value in emails if (email := normalize_email(value))})
    if not normalized:
        return set()
    found: set[str] = set()
    async with httpx.AsyncClient(timeout=20) as client:
        for offset in range(0, len(normalized), _CHUNK_SIZE):
            chunk = normalized[offset:offset + _CHUNK_SIZE]
            response = await client.get(f"{_base(settings)}/email_suppressions", params={
                "select": "email", "email": f"in.({','.join(chunk)})", "active": "eq.true",
                "limit": str(len(chunk)),
            }, headers=_headers(settings))
            if not response.is_success:
                raise HTTPException(status_code=503, detail="Could not check the email suppression list. Apply migration 020 and retry.")
            try:
                rows = response.json()
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=503, detail="Could not check the email suppression list.") from exc
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise HTTPException(status_code=503, detail="Could not check the email suppression list.")
            found.update(str(row["email"]).lower() for row in rows if row.get("email"))
    return found


async def save_suppressions(settings: Settings, records: list[dict]) -> int:
    """Record new opt-outs and reactivations atomically with audit events."""
    payload = []
    for record in records:
        email = normalize_email(record.get("email"))
        source = record.get("source")
        if not email or source not in {"reply_auto", "reply_manual"}:
            continue
        payload.append({
            "email": email,
            "reason": str(record.get("reason") or "Recipient requested no further email.")[:300],
            "source": source,
            "lead_id": record.get("lead_id"),
            "campaign_id": record.get("campaign_id"),
            "actor_user_id": record.get("actor_user_id"),
        })
    if not payload:
        return 0
    saved = 0
    async with httpx.AsyncClient(timeout=25) as client:
        for offset in range(0, len(payload), _CHUNK_SIZE):
            chunk = payload[offset:offset + _CHUNK_SIZE]
            response = await client.post(f"{_base(settings)}/rpc/record_email_suppressions",
                headers=_headers(settings), json={"p_records": chunk})
            if not response.is_success:
                raise HTTPException(status_code=503, detail="Could not save the email opt-out. Apply migration 020 and retry.")
            try:
                inserted = response.json()
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=503, detail="Could not confirm the email opt-out was saved.") from exc
            if type(inserted) is not int or inserted < 0:
                raise HTTPException(status_code=503, detail="Could not confirm the email opt-out was saved.")
            saved += inserted
    return saved


async def list_suppression_management(settings: Settings, *, limit: int, offset: int) -> dict:
    base = _base(settings)
    async with httpx.AsyncClient(timeout=25) as client:
        suppressions_response = await client.get(f"{base}/email_suppressions", params={
            "select": "email,reason,source,lead_id,campaign_id,active,created_at,released_at,released_by,release_reason",
            "order": "active.desc,created_at.desc", "limit": str(limit), "offset": str(offset),
        }, headers=_headers(settings))
        if not suppressions_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load suppression records. Apply migration 020 and retry.")
        events_response = await client.get(f"{base}/email_suppression_events", params={
            "select": "id,email,event_type,source,reason,actor_user_id,created_at",
            "order": "created_at.desc", "limit": "100",
        }, headers=_headers(settings))
        if not events_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load suppression audit history. Apply migration 020 and retry.")
        try:
            suppressions, events = suppressions_response.json(), events_response.json()
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=503, detail="Could not load suppression management data.") from exc
    if not isinstance(suppressions, list) or not isinstance(events, list):
        raise HTTPException(status_code=503, detail="Could not load suppression management data.")
    return {"items": suppressions, "events": events}


async def release_suppression(settings: Settings, email: str, reason: str, actor_user_id: str) -> dict:
    normalized = normalize_email(email)
    if not normalized:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    reason = reason.strip()
    if len(reason) < 10:
        raise HTTPException(status_code=422, detail="Record why the recipient explicitly asked to resume emails.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{_base(settings)}/rpc/release_email_suppression", headers=_headers(settings),
                                     json={"target_email": normalized, "release_reason": reason[:300],
                                           "actor_id": actor_user_id})
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Suppression record not found.")
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not release this suppression. Apply migration 020 and retry.")
    try:
        rows = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Could not confirm suppression release.") from exc
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=503, detail="Could not confirm suppression release.")
    return rows[0]
