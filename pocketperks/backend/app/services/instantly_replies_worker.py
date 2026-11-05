"""Low-frequency background poller for received Instantly replies."""
import asyncio
import logging

from app.config import Settings
from app.instantly_replies_service import sync_recent_replies
from app.instantly_sent_sync_service import sync_sent_email_statuses

logger = logging.getLogger(__name__)
POLL_SECONDS = 60


async def run_instantly_replies_worker(settings: Settings) -> None:
    campaign_offset = 0
    while True:
        try:
            if settings.instantly_api_key:
                try:
                    sent = await sync_sent_email_statuses(settings, campaign_offset=campaign_offset)
                    if sent["campaigns_available"]:
                        campaign_offset = (campaign_offset + sent["campaigns_scanned"]) % sent["campaigns_available"]
                    if sent["marked_sent"]:
                        logger.info("Reconciled %s confirmed Instantly sends into PocketPerks.", sent["marked_sent"])
                except Exception as exc:
                    logger.warning("Instantly sent-email status sync will retry after %s.", type(exc).__name__)
                result = await sync_recent_replies(settings, max_pages=1)
                if result["saved"]:
                    logger.info("Saved %s new Instantly reply/replies.", result["saved"])
            await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Instantly reply sync will retry after %s.", type(exc).__name__)
            await asyncio.sleep(POLL_SECONDS)
