"""Database facade for the human approval queue (PostgreSQL only)."""
from fastapi import HTTPException

from app.config import Settings
from app.approvals_postgres import (
    clear_history,
    list_history,
    list_pending,
    review_context,
    update_email,
)


def _required_pool(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    return pool


async def list_pending_approvals(settings: Settings, *, limit: int, offset: int, pool=None) -> dict:
    return await list_pending(_required_pool(pool), limit, offset)


async def list_review_history(settings: Settings, *, limit: int, offset: int,
                              search: str = "", days: int = 30, pool=None) -> dict:
    """Return a filtered, paged view of immutable automated and human audit events."""
    return await list_history(_required_pool(pool), limit, offset, search, days)


async def clear_review_history(settings: Settings, pool=None) -> dict:
    """Clear the audit log only; personalized emails and campaign data are untouched."""
    return await clear_history(_required_pool(pool))


async def get_email_review_context(settings: Settings, email_id: str, pool=None) -> dict | None:
    return await review_context(_required_pool(pool), email_id)


async def update_review_email(settings: Settings, email_id: str, expected_status: str, values: dict, pool=None) -> dict:
    return await update_email(_required_pool(pool), email_id, expected_status, values)
