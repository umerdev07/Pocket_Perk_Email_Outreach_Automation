"""PostgreSQL persistence facade for Instantly provider mappings."""

from fastapi import HTTPException

from app.config import Settings


def _pool_or_fail(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable for Instantly sync.")
    return pool


async def create_campaign_mapping(settings: Settings, run_id: str, sender_emails=None, pool=None) -> dict:
    from app.instantly_sync_postgres import create_campaign_mapping as fn
    return await fn(_pool_or_fail(pool), run_id, sender_emails)


async def update_campaign_mapping(settings: Settings, mapping_id: str, values: dict, pool=None) -> dict:
    from app.instantly_sync_postgres import update_campaign_mapping as fn
    return await fn(_pool_or_fail(pool), mapping_id, values)


async def get_campaign_mapping_for_run(settings: Settings, run_id: str, pool=None) -> dict | None:
    from app.instantly_sync_postgres import get_campaign_mapping_for_run as fn
    return await fn(_pool_or_fail(pool), run_id)


async def load_active_sender_usage(settings: Settings, pool=None) -> dict:
    from app.instantly_sync_postgres import load_active_sender_usage as fn
    return await fn(_pool_or_fail(pool))


async def load_scheduled_campaign_windows(settings: Settings, pool=None) -> list[dict]:
    from app.instantly_sync_postgres import load_scheduled_campaign_windows as fn
    return await fn(_pool_or_fail(pool))


async def save_lead_mappings(settings: Settings, mapping_id: str, rows: list[dict], pool=None) -> int:
    from app.instantly_sync_postgres import save_lead_mappings as fn
    return await fn(_pool_or_fail(pool), mapping_id, rows)


async def get_lead_mapping_statuses(settings: Settings, mapping_id: str, lead_ids: list[str], pool=None) -> dict:
    from app.instantly_sync_postgres import get_lead_mapping_statuses as fn
    return await fn(_pool_or_fail(pool), mapping_id, lead_ids)


async def load_sync_run_context(settings: Settings, run_id: str, pool=None) -> dict:
    from app.instantly_sync_postgres import load_sync_run_context as fn
    return await fn(_pool_or_fail(pool), run_id)


async def load_sync_lead_page(settings: Settings, run_id: str, batch_id: str, *, offset: int,
                              limit: int, pool=None) -> list[dict]:
    from app.instantly_sync_postgres import load_sync_lead_page as fn
    return await fn(_pool_or_fail(pool), run_id, batch_id, offset=offset, limit=limit)


async def load_active_campaign_windows(settings: Settings, *, limit: int = 100, pool=None) -> list[dict]:
    from app.instantly_sync_postgres import load_active_campaign_windows as fn
    return await fn(_pool_or_fail(pool), limit=limit)


async def load_active_runs_with_pending_leads(settings: Settings, *, limit: int = 100, pool=None) -> list[str]:
    from app.instantly_sync_postgres import load_active_runs_with_pending_leads as fn
    return await fn(_pool_or_fail(pool), limit=limit,
                    auto_launch=settings.instantly_auto_launch and settings.send_mode == "live")
