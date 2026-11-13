"""Durable queue for provider webhooks, deduplicated by raw payload digest."""
from datetime import UTC, datetime

from fastapi import HTTPException

from app.config import Settings


def _required_pool(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    return pool


async def enqueue_instantly_webhook(settings: Settings, event_key: str, payload: dict, pool=None) -> bool:
    async with _required_pool(pool).acquire() as conn:
        row = await conn.fetchrow("""insert into public.instantly_webhook_events(event_key,payload)
            values($1,$2::jsonb) on conflict(event_key) do nothing returning id""", event_key, payload)
    return row is not None


async def claim_instantly_webhooks(settings: Settings, limit: int = 10, pool=None) -> list[dict]:
    if not 1 <= limit <= 50:
        raise ValueError("Webhook claim limit must be between 1 and 50.")
    async with _required_pool(pool).acquire() as conn:
        rows = await conn.fetch("""with candidates as (
            select id from public.instantly_webhook_events
            where status='pending' or (status='processing' and updated_at<now()-interval '10 minutes')
            order by received_at,id for update skip locked limit $1)
            update public.instantly_webhook_events e
            set status='processing',attempts=e.attempts+1,error_detail=null,updated_at=now()
            from candidates c where e.id=c.id returning e.id,e.event_key,e.payload,e.attempts""", limit)
    return [dict(row) for row in rows]


async def finish_instantly_webhook(settings: Settings, event_id: int, *, error: str | None = None,
                                   attempts: int = 1, pool=None) -> None:
    status = "processed" if error is None else "pending" if attempts < 10 else "failed"
    processed_at = datetime.now(UTC) if error is None else None
    error_detail = error[:160] if error else None
    async with _required_pool(pool).acquire() as conn:
        await conn.execute("""update public.instantly_webhook_events set status=$2,processed_at=$3,
            error_detail=$4,payload=case when $2='processed' then '{}'::jsonb else payload end,updated_at=now()
            where id=$1 and status='processing'""", event_id, status, processed_at, error_detail)
