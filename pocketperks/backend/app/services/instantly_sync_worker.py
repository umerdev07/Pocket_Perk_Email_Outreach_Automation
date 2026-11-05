"""Durable background worker for inactive Instantly drafts and lead sync."""
import asyncio
import logging

from fastapi import HTTPException

from app.config import Settings
from app.queue_db import (archive_instantly_sync_job, extend_instantly_sync_job,
                          read_instantly_sync_jobs)
from app.instantly_sync_service import sync_completed_run

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300
LEASE_REFRESH_SECONDS = 45
POLL_SECONDS = 4
MAX_ATTEMPTS = 3


async def _keep_lease(settings: Settings, message_id: int) -> None:
    while True:
        await asyncio.sleep(LEASE_REFRESH_SECONDS)
        if not await extend_instantly_sync_job(settings, message_id, visibility_seconds=LEASE_SECONDS):
            logger.warning("Instantly sync queue lease was not renewed (message_id=%s).", message_id)
            return


async def _consume(settings: Settings, job: dict) -> None:
    message_id = int(job["message_id"])
    read_count = int(job.get("read_count") or 1)
    message = job.get("message") or {}
    run_id = message.get("run_id") if isinstance(message, dict) else None
    if not isinstance(run_id, str):
        logger.error("Ignoring malformed Instantly sync message (message_id=%s).", message_id)
        await archive_instantly_sync_job(settings, message_id)
        return

    lease_task = asyncio.create_task(_keep_lease(settings, message_id))
    try:
        result = await sync_completed_run(settings, run_id)
        logger.info("Instantly draft sync completed for run %s (synced=%s, skipped=%s).",
                    run_id, result["synced"], result["skipped"])
        await archive_instantly_sync_job(settings, message_id)
    except HTTPException as exc:
        if exc.status_code == 409:
            # Approval can be queued while personalization is still running.
            # Leave this message leased briefly; the same run will be ready later.
            await extend_instantly_sync_job(settings, message_id, visibility_seconds=30)
        elif read_count >= MAX_ATTEMPTS:
            logger.error("Instantly sync stopped for run %s after setup/database failures (%s).",
                         run_id, exc.status_code)
            await archive_instantly_sync_job(settings, message_id)
        else:
            await extend_instantly_sync_job(settings, message_id,
                                            visibility_seconds=min(60 * read_count, LEASE_SECONDS))
            logger.warning("Instantly sync will retry for run %s (attempt=%s).", run_id, read_count)
    except Exception as exc:
        if read_count >= MAX_ATTEMPTS:
            logger.error("Instantly sync stopped for run %s after %s (%s).",
                         run_id, type(exc).__name__, read_count)
            await archive_instantly_sync_job(settings, message_id)
        else:
            await extend_instantly_sync_job(settings, message_id,
                                            visibility_seconds=min(60 * read_count, LEASE_SECONDS))
            logger.warning("Instantly sync will retry for run %s after %s (attempt=%s).",
                           run_id, type(exc).__name__, read_count)
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)


async def run_instantly_sync_worker(settings: Settings) -> None:
    """Poll PGMQ; keep queued work untouched until the API key is configured."""
    while True:
        try:
            if not settings.instantly_api_key:
                await asyncio.sleep(POLL_SECONDS)
                continue
            jobs = await read_instantly_sync_jobs(settings, visibility_seconds=LEASE_SECONDS, quantity=1)
            if not jobs:
                await asyncio.sleep(POLL_SECONDS)
                continue
            await _consume(settings, jobs[0])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Instantly sync worker error (%s); retrying shortly.", type(exc).__name__)
            await asyncio.sleep(POLL_SECONDS)
