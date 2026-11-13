"""PostgreSQL dashboard repository facade."""

from fastapi import HTTPException

from app.config import Settings


def _require_pool(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    return pool


async def get_dashboard_run_detail(settings: Settings, run_id: str, pool=None) -> dict:
    from app.dashboard_postgres import get_dashboard_run_detail as fn
    return await fn(_require_pool(pool), run_id)


async def get_dashboard_summary(settings: Settings, *, activity_days: int = 14, pool=None) -> dict:
    if type(activity_days) is not int or not 7 <= activity_days <= 90:
        raise ValueError("Dashboard activity must be between 7 and 90 days.")
    from app.dashboard_postgres import get_dashboard_summary as fn
    return await fn(_require_pool(pool), settings, activity_days=activity_days)
