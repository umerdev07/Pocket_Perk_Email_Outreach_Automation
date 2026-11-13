"""Service-role access to the workspace-wide email suppression list."""

import re

from fastapi import HTTPException

from app.config import Settings

_EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.I)
_CHUNK_SIZE = 200


def normalize_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    return email if _EMAIL_RE.fullmatch(email) else None


def _required_pool(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    return pool


async def get_suppressed_emails(settings: Settings, emails: set[str] | list[str], pool=None) -> set[str]:
    """Fail closed: a missing migration or DB error must not permit a send."""
    normalized = sorted({email for value in emails if (email := normalize_email(value))})
    if not normalized:
        return set()
    from app.personalization_postgres import get_suppressed_emails as pg_get
    return await pg_get(_required_pool(pool), set(normalized))


async def get_recorded_suppression_emails(settings: Settings, emails: set[str] | list[str], pool=None) -> set[str]:
    """Return addresses with any suppression record, including released ones.

    Historical reply reconciliation must not reactivate a deliberately released
    address just because its old opt-out reply is still stored.
    """
    normalized = sorted({email for value in emails if (email := normalize_email(value))})
    if not normalized:
        return set()
    async with _required_pool(pool).acquire() as conn:
        rows = await conn.fetch("select email from public.email_suppressions where email=any($1::text[])", normalized)
    return {row["email"] for row in rows}


async def save_suppressions(settings: Settings, records: list[dict], pool=None) -> int:
    """Record new opt-outs and reactivations atomically with audit events."""
    payload = []
    for record in records:
        email = normalize_email(record.get("email"))
        source = record.get("source")
        if not email or source not in {"reply_auto", "reply_manual", "bounce_auto"}:
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
    async with _required_pool(pool).acquire() as conn:
        return int(await conn.fetchval("select public.record_email_suppressions($1::jsonb)", payload))


async def list_suppression_management(settings: Settings, *, limit: int, offset: int, pool=None) -> dict:
    async with _required_pool(pool).acquire() as conn:
        records = await conn.fetch("select email,reason,source,lead_id,campaign_id,active,created_at,released_at,released_by,release_reason from public.email_suppressions order by active desc,created_at desc limit $1 offset $2", limit, offset)
        events = await conn.fetch("select id,email,event_type,source,reason,actor_user_id,created_at from public.email_suppression_events order by created_at desc limit 100")
    return {"items": [dict(row) for row in records], "events": [dict(row) for row in events]}


async def release_suppression(settings: Settings, email: str, reason: str, actor_user_id: str,
                              *, recipient_confirmed: bool = False,
                              bounce_address_corrected: bool = False, pool=None) -> dict:
    normalized = normalize_email(email)
    if not normalized:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    reason = reason.strip()
    if len(reason) < 10:
        raise HTTPException(status_code=422, detail="Add a clear audit reason before clearing this block.")
    async with _required_pool(pool).acquire() as conn:
        current = await conn.fetchrow("select source,active from public.email_suppressions where email=$1", normalized)
        if not current:
            raise HTTPException(status_code=404, detail="Suppression record not found.")
        if current["source"] == "bounce_auto" and not bounce_address_corrected:
            raise HTTPException(status_code=422, detail="Confirm that the address was corrected and re-verified before clearing a bounce block.")
        if current["source"] != "bounce_auto" and not recipient_confirmed:
            raise HTTPException(status_code=422, detail="Confirm that the recipient explicitly asked to receive emails again.")
        try:
            row = await conn.fetchrow("select * from public.release_email_suppression($1,$2,$3::uuid)", normalized, reason[:300], actor_user_id)
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "P0002":
                raise HTTPException(status_code=404, detail="Suppression record not found.") from exc
            raise
    return dict(row)
