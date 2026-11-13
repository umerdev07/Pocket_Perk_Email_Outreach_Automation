"""Native PostgreSQL durable queue primitives; callers pass an asyncpg pool."""

from typing import Any


_QUEUE_NAMES = {"personalization", "instantly_sync"}


def _validate_queue(queue_name: str) -> None:
    if queue_name not in _QUEUE_NAMES:
        raise ValueError("Unknown background queue.")


async def enqueue_job(pool, queue_name: str, payload: dict[str, Any]) -> int:
    _validate_queue(queue_name)
    row_id = await pool.fetchval(
        """insert into public.background_jobs(queue_name, payload)
           values ($1, $2::jsonb) returning id""",
        queue_name, payload,
    )
    return int(row_id)


async def claim_jobs(
    pool, queue_name: str, *, quantity: int = 1, lease_seconds: int = 120, max_attempts: int = 8
) -> list[dict]:
    """Atomically claim pending or lease-expired jobs without double-claiming."""
    _validate_queue(queue_name)
    if not 1 <= quantity <= 100 or not 1 <= lease_seconds <= 3600 or not 1 <= max_attempts <= 100:
        raise ValueError("Queue quantity, lease duration, or attempt limit is outside the allowed range.")
    rows = await pool.fetch(
        """with exhausted as (
             update public.background_jobs
                set status = 'dead', lease_expires_at = null,
                    last_error = coalesce(last_error, 'Maximum processing attempts exceeded.'),
                    updated_at = now()
              where queue_name = $1 and status = 'processing'
                and lease_expires_at <= now() and attempts >= $4
             returning id
           ), candidates as (
             select id
             from public.background_jobs
             where queue_name = $1
               and ((status = 'pending' and available_at <= now())
                    or (status = 'processing' and lease_expires_at <= now() and attempts < $4))
             order by available_at, id
             limit $2
             for update skip locked
           )
           update public.background_jobs as jobs
              set status = 'processing', attempts = jobs.attempts + 1,
                  lease_expires_at = now() + make_interval(secs => $3), updated_at = now()
             from candidates
            where jobs.id = candidates.id
           returning jobs.id, jobs.attempts, jobs.payload""",
        queue_name, quantity, lease_seconds, max_attempts,
    )
    claimed = []
    for row in rows:
        payload = row["payload"]
        claimed.append({"message_id": int(row["id"]), "read_count": int(row["attempts"]), "message": payload})
    return claimed


async def extend_job_lease(pool, job_id: int, *, lease_seconds: int = 120) -> bool:
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("Lease duration is outside the allowed range.")
    row = await pool.fetchrow(
        """update public.background_jobs
              set lease_expires_at = now() + make_interval(secs => $2), updated_at = now()
            where id = $1 and status = 'processing' and lease_expires_at > now()
        returning id""",
        job_id, lease_seconds,
    )
    return row is not None


async def complete_job(pool, job_id: int) -> bool:
    row = await pool.fetchrow(
        """update public.background_jobs
              set status = 'completed', lease_expires_at = null, completed_at = now(), updated_at = now()
            where id = $1 and status = 'processing' and lease_expires_at > now()
        returning id""",
        job_id,
    )
    return row is not None


async def retry_job(pool, job_id: int, error: str, *, delay_seconds: int = 60, max_attempts: int = 8) -> str | None:
    if not 0 <= delay_seconds <= 86400 or not 1 <= max_attempts <= 100:
        raise ValueError("Retry delay or attempt limit is outside the allowed range.")
    row = await pool.fetchrow(
        """update public.background_jobs
              set status = case when attempts >= $4 then 'dead' else 'pending' end,
                  available_at = now() + make_interval(secs => $3), lease_expires_at = null,
                  last_error = left($2, 4000), updated_at = now()
            where id = $1 and status = 'processing' and lease_expires_at > now()
        returning status""",
        job_id, error, delay_seconds, max_attempts,
    )
    return row["status"] if row else None
