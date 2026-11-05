"""Low-frequency background poller for received Instantly replies."""
import asyncio
import logging

from app.config import Settings
from app.instantly_replies_service import sync_recent_replies

logger = logging.getLogger(__name__)
POLL_SECONDS = 60


async def run_instantly_replies_worker(settings: Settings) -> None:
    while True:
        try:
            if settings.instantly_api_key:
                result = await sync_recent_replies(settings, max_pages=1)
                if result["saved"]:
                    logger.info("Saved %s new Instantly reply/replies.", result["saved"])
            await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Instantly reply sync will retry after %s.", type(exc).__name__)
            await asyncio.sleep(POLL_SECONDS)
