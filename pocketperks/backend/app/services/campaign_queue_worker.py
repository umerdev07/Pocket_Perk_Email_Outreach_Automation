"""Consumes durable PGMQ messages and resumes interrupted campaign runs."""
import asyncio
import logging

from app.campaign_runs_db import get_campaign_run
from app.config import Settings
from app.queue_db import (
    archive_personalization_job,
    extend_personalization_job,
    read_personalization_jobs,
    reset_run_for_retry,
)
from app.services.personalization_service import process_campaign_run

logger = logging.getLogger(__name__)
VISIBILITY_SECONDS = 120
LEASE_REFRESH_SECONDS = 30
MAX_DELIVERY_ATTEMPTS = 3
EMPTY_QUEUE_SLEEP_SECONDS = 3
RETRYABLE_RATE_LIMIT_DETAIL = (
    "Claude API rate limit persisted after retries. Incomplete email steps will be retried."
)


async def _keep_lease(settings: Settings, message_id: int, pool=None) -> None:
    while True:
        await asyncio.sleep(LEASE_REFRESH_SECONDS)
        if not await extend_personalization_job(settings, message_id, visibility_seconds=VISIBILITY_SECONDS,pool=pool):
            logger.warning("Personalization job lease was not renewed (message_id=%s).", message_id)
            return


async def _consume_job(settings: Settings, job: dict, pool=None) -> None:
    message_id = int(job["message_id"])
    read_count = int(job.get("read_count") or 1)
    message = job.get("message") or {}
    run_id = message.get("run_id") if isinstance(message, dict) else None
    if not isinstance(run_id, str):
        logger.error("Ignoring malformed personalization queue message (message_id=%s).", message_id)
        await archive_personalization_job(settings, message_id,pool=pool)
        return

    lease_task = asyncio.create_task(_keep_lease(settings, message_id,pool=pool))
    try:
        logger.info("Processing queued campaign run %s (delivery %s).", run_id, read_count)
        await process_campaign_run(settings, run_id,pool=pool)
        run = await get_campaign_run(settings, run_id,pool=pool)
        if run is None:
            logger.error("Campaign run %s was removed while queued; archiving its message.", run_id)
            await archive_personalization_job(settings, message_id,pool=pool)
        elif run["status"] == "completed":
            await archive_personalization_job(settings, message_id,pool=pool)
        elif (run["status"] == "failed"
              and run.get("error_detail") == RETRYABLE_RATE_LIMIT_DETAIL
              and read_count < MAX_DELIVERY_ATTEMPTS):
            await reset_run_for_retry(settings, run_id,pool=pool)
            backoff_seconds = min(300 * read_count, 900)
            await extend_personalization_job(settings, message_id, visibility_seconds=backoff_seconds,pool=pool)
            logger.warning("Campaign run %s will retry after a transient failure (backoff=%s seconds).",
                           run_id, backoff_seconds)
            # Leave the PGMQ message unacknowledged; it becomes visible again
            # after the current visibility window and is retried by any worker.
        elif run["status"] == "failed":
            # A permanent/setup failure must remain visible to the user with its
            # original error. Retrying every failed run used to clear its counters
            # and error_detail, leaving it looking "running" forever.
            await archive_personalization_job(settings, message_id,pool=pool)
            logger.error("Campaign run %s failed permanently; its queue message was archived.", run_id)
        else:
            await archive_personalization_job(settings, message_id,pool=pool)
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)


async def run_campaign_queue_worker(settings: Settings, pool=None) -> None:
    """Poll PGMQ. Messages survive API restarts and are leased to one worker."""
    while True:
        try:
            jobs = await read_personalization_jobs(settings, visibility_seconds=VISIBILITY_SECONDS, quantity=1,pool=pool)
            if not jobs:
                await asyncio.sleep(EMPTY_QUEUE_SLEEP_SECONDS)
                continue
            await _consume_job(settings, jobs[0],pool=pool)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Log only the exception type. HTTP/provider error payloads may carry
            # sensitive details and are not needed to identify the failure class.
            logger.error("Personalization queue worker error (%s). Retrying shortly.", type(exc).__name__)
            await asyncio.sleep(EMPTY_QUEUE_SLEEP_SECONDS)
