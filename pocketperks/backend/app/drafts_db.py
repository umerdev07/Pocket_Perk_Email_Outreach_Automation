"""Compatibility facade for PostgreSQL-backed campaign draft persistence."""

from fastapi import HTTPException

from app.config import Settings


def _require_pool(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable for campaign drafts.")
    return pool


async def load_campaign(settings: Settings, campaign_id: str, pool=None) -> dict | None:
    from app.drafts_postgres import load_campaign as fn
    return await fn(_require_pool(pool), campaign_id)


async def upsert_step_draft(settings: Settings, step_id: str, subject: str, body: str, pool=None) -> dict:
    from app.drafts_postgres import upsert_step_draft as fn
    return await fn(_require_pool(pool), step_id, subject, body)


async def get_draft_context(settings: Settings, draft_id: str, pool=None) -> dict | None:
    from app.drafts_postgres import get_draft_context as fn
    return await fn(_require_pool(pool), draft_id)


async def prepare_step_draft(settings: Settings, step_id: str, pool=None) -> dict:
    from app.drafts_postgres import prepare_step_draft as fn
    return await fn(_require_pool(pool), step_id)


async def set_generation_state(settings: Settings, draft_id: str, state: str,
                               error: str | None = None, pool=None) -> dict:
    from app.drafts_postgres import set_generation_state as fn
    return await fn(_require_pool(pool), draft_id, state, error)


async def demote_campaign(settings: Settings, campaign_id: str, pool=None) -> None:
    async with _require_pool(pool).acquire() as conn:
        await conn.execute("update public.campaigns set status='draft' where id=$1::uuid", campaign_id)


async def update_draft(settings: Settings, draft_id: str, subject: str, body: str, pool=None) -> dict:
    from app.drafts_postgres import update_draft as fn
    return await fn(_require_pool(pool), draft_id, subject, body)


async def approve_draft(settings: Settings, draft_id: str, pool=None) -> dict:
    from app.drafts_postgres import approve_draft as fn
    return await fn(_require_pool(pool), draft_id)
