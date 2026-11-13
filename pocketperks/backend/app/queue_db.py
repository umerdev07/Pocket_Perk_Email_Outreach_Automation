"""PostgreSQL-backed durable worker queue operations."""

from fastapi import HTTPException

from app.config import Settings


def _pool_or_fail(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL queue is unavailable.")
    return pool


async def read_personalization_jobs(settings: Settings, *, visibility_seconds: int = 120,
                                   quantity: int = 1, pool=None) -> list[dict]:
    from app.postgres_queue import claim_jobs
    return await claim_jobs(_pool_or_fail(pool), "personalization", quantity=quantity,
                            lease_seconds=visibility_seconds)


async def extend_personalization_job(settings: Settings, message_id: int, *, visibility_seconds: int = 120,
                                     pool=None) -> bool:
    from app.postgres_queue import extend_job_lease
    return await extend_job_lease(_pool_or_fail(pool), message_id, lease_seconds=visibility_seconds)


async def archive_personalization_job(settings: Settings, message_id: int, pool=None) -> bool:
    from app.postgres_queue import complete_job
    return await complete_job(_pool_or_fail(pool), message_id)


async def reset_run_for_retry(settings: Settings, run_id: str, pool=None) -> None:
    async with _pool_or_fail(pool).acquire() as conn:
        await conn.execute("""update public.campaign_runs set status='running',processed=0,skipped=0,failed=0,
            finished_at=null,error_detail=null,skip_log='[]'::jsonb,failure_log='[]'::jsonb where id=$1::uuid""", run_id)


async def read_instantly_sync_jobs(settings: Settings, *, visibility_seconds: int = 300,
                                  quantity: int = 1, pool=None) -> list[dict]:
    from app.postgres_queue import claim_jobs
    return await claim_jobs(_pool_or_fail(pool), "instantly_sync", quantity=quantity,
                            lease_seconds=visibility_seconds)


async def extend_instantly_sync_job(settings: Settings, message_id: int, *, visibility_seconds: int = 300,
                                    pool=None) -> bool:
    from app.postgres_queue import extend_job_lease
    return await extend_job_lease(_pool_or_fail(pool), message_id, lease_seconds=visibility_seconds)


async def archive_instantly_sync_job(settings: Settings, message_id: int, pool=None) -> bool:
    from app.postgres_queue import complete_job
    return await complete_job(_pool_or_fail(pool), message_id)
