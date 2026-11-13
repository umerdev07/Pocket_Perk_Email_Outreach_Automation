"""PostgreSQL persistence facade for campaign personalization."""

from fastapi import HTTPException

from app.config import Settings


def _require_pool(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable for personalization.")
    return pool


async def load_run_context(settings: Settings, run_id: str, pool=None) -> dict:
    from app.personalization_postgres import load_run_context as fn
    return await fn(_require_pool(pool), run_id)


async def load_lead_page(settings: Settings, batch_id: str, *, offset: int, limit: int, pool=None) -> list[dict]:
    from app.personalization_postgres import load_lead_page as fn
    return await fn(_require_pool(pool), batch_id, offset=offset, limit=limit)


async def get_run_personalized_emails(settings: Settings, run_id: str, *, limit: int, offset: int, pool=None) -> dict:
    from app.personalization_postgres import get_run_personalized_emails as fn
    return await fn(_require_pool(pool), run_id, limit=limit, offset=offset)


async def get_run_lead_personalized_emails(settings: Settings, run_id: str, lead_id: str, pool=None) -> dict:
    from app.personalization_postgres import get_run_lead_personalized_emails as fn
    return await fn(_require_pool(pool), run_id, lead_id)


async def save_personalized_email(settings: Settings, email: dict, pool=None) -> dict:
    from app.personalization_postgres import save_personalized_email as fn
    return await fn(_require_pool(pool), email)


async def get_existing_personalization_keys(settings: Settings, run_id: str,
                                            lead_ids: list[str], pool=None) -> set[tuple[str, str]]:
    from app.personalization_postgres import get_existing_personalization_keys as fn
    return await fn(_require_pool(pool), run_id, lead_ids)


async def update_run_progress(settings: Settings, run_id: str, *, processed: int, skipped: int,
                              failed: int, skip_log: list[dict] | None, status: str, finished: bool = False,
                              error_detail: str | None = None, failure_log: list[dict] | None = None,
                              pool=None) -> None:
    from app.personalization_postgres import update_run_progress as fn
    return await fn(_require_pool(pool), run_id, processed=processed, skipped=skipped, failed=failed,
                    skip_log=skip_log, status=status, finished=finished,
                    error_detail=error_detail, failure_log=failure_log)


async def get_suppressed_emails(settings: Settings, emails: set[str] | list[str], pool=None) -> set[str]:
    from app.personalization_postgres import get_suppressed_emails as fn
    return await fn(_require_pool(pool), emails)
