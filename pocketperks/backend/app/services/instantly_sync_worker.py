"""Durable background worker for Instantly draft, lead sync, and gated launch."""
import asyncio
import logging
from datetime import UTC, datetime
import math

from fastapi import HTTPException

from app.config import Settings
from app.queue_db import (archive_instantly_sync_job, extend_instantly_sync_job,
                          read_instantly_sync_jobs)
from app.instantly_sync_db import load_active_runs_with_pending_leads
from app.instantly_sync_service import sync_completed_run

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300
LEASE_REFRESH_SECONDS = 45
POLL_SECONDS = 4
MAX_ATTEMPTS = 3
RECOVERY_INTERVAL_SECONDS = 60


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
        deferred_until = result.get("launch_deferred_until")
        if isinstance(deferred_until, str):
            launch_at = datetime.fromisoformat(deferred_until.replace("Z", "+00:00"))
            seconds_until_launch = max(30, math.ceil((launch_at - datetime.now(UTC)).total_seconds()))
            # Recheck at least daily so long schedules remain restart-safe and bounded.
            await extend_instantly_sync_job(settings, message_id,
                                            visibility_seconds=min(seconds_until_launch, 86_400))
            logger.info("Instantly campaign for run %s is synced and scheduled for its start time.", run_id)
            return
        logger.info("Instantly sync finished for run %s (synced=%s, skipped=%s, launch=%s).",
                    run_id, result["synced"], result["skipped"], result.get("launch", "not_requested"))
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
    next_recovery_at = 0.0
    while True:
        try:
            if not settings.instantly_api_key:
                await asyncio.sleep(POLL_SECONDS)
                continue

            now = asyncio.get_running_loop().time()
            if now >= next_recovery_at:
                next_recovery_at = now + RECOVERY_INTERVAL_SECONDS
                run_ids = await load_active_runs_with_pending_leads(settings)
                for run_id in run_ids:
                    try:
                        result = await sync_completed_run(settings, run_id)
                        if result.get("synced", 0):
                            logger.info("Instantly recovery synced pending leads for active run %s (synced=%s).",
                                        run_id, result["synced"])
                    except Exception as exc:
                        logger.warning("Instantly recovery will retry active run %s after %s.",
                                       run_id, type(exc).__name__)

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
