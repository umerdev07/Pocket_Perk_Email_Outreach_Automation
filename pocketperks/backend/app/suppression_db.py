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
                "select": "email", "email": f"in.({','.join(chunk)})", "limit": str(len(chunk)),
            }, headers=_headers(settings))
            if not response.is_success:
                raise HTTPException(status_code=503, detail="Could not check the email suppression list. Apply migration 019 and retry.")
            try:
                rows = response.json()
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=503, detail="Could not check the email suppression list.") from exc
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise HTTPException(status_code=503, detail="Could not check the email suppression list.")
            found.update(str(row["email"]).lower() for row in rows if row.get("email"))
    return found


async def save_suppressions(settings: Settings, records: list[dict]) -> int:
    """Insert opt-outs idempotently; the first captured reason/source is retained."""
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
        })
    if not payload:
        return 0
    saved = 0
    async with httpx.AsyncClient(timeout=25) as client:
        for offset in range(0, len(payload), _CHUNK_SIZE):
            chunk = payload[offset:offset + _CHUNK_SIZE]
            response = await client.post(f"{_base(settings)}/email_suppressions",
                params={"on_conflict": "email"},
                headers=_headers(settings, "resolution=ignore-duplicates,return=representation"), json=chunk)
            if not response.is_success:
                raise HTTPException(status_code=503, detail="Could not save the email opt-out. Apply migration 019 and retry.")
            try:
                rows = response.json()
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=503, detail="Could not confirm the email opt-out was saved.") from exc
            if not isinstance(rows, list):
                raise HTTPException(status_code=503, detail="Could not confirm the email opt-out was saved.")
            saved += len(rows)
    return saved
