"""Periodic durable-enough lifecycle enforcement for active campaigns."""
import asyncio
import logging
import time

from app.config import Settings
from app.services.instantly_campaign_lifecycle import (activate_scheduled_campaigns,
                                                       pause_campaigns_past_end_time,
                                                       reconcile_completed_campaigns)

logger = logging.getLogger(__name__)
POLL_SECONDS = 30
STATUS_RECONCILE_SECONDS = 300


async def run_instantly_lifecycle_worker(settings: Settings) -> None:
    last_status_reconcile = 0.0
    while True:
        try:
            if settings.instantly_api_key:
                await activate_scheduled_campaigns(settings)
                await pause_campaigns_past_end_time(settings)
                now = time.monotonic()
                if now - last_status_reconcile >= STATUS_RECONCILE_SECONDS:
                    await reconcile_completed_campaigns(settings)
                    last_status_reconcile = now
            await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Instantly campaign lifecycle check will retry (%s).", type(exc).__name__)
            await asyncio.sleep(POLL_SECONDS)
