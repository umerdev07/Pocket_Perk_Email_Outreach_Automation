"""Enforce exact campaign end timestamps for auto-launched Instantly campaigns."""
import logging
from datetime import UTC, datetime

from app.config import Settings
from app.instantly_sync_db import load_active_campaign_windows, update_campaign_mapping
from app.integrations.instantly_client import pause_campaign

logger = logging.getLogger(__name__)


async def pause_campaigns_past_end_time(settings: Settings) -> int:
    """Pause expired campaigns and persist their completed mapping state."""
    now = datetime.now(UTC)
    mappings = await load_active_campaign_windows(settings)
    paused = 0
    for mapping in mappings:
        try:
            end_at = datetime.fromisoformat(str(mapping["end_at"]).replace("Z", "+00:00"))
            if end_at.tzinfo is None:
                raise ValueError("missing timezone")
        except (KeyError, TypeError, ValueError):
            logger.error("Auto-launched Instantly campaign has an invalid end time (mapping_id=%s).", mapping.get("id"))
            continue
        if end_at.astimezone(UTC) > now:
            continue
        try:
            await pause_campaign(str(mapping["instantly_campaign_id"]), settings)
            await update_campaign_mapping(settings, str(mapping["id"]), {
                "status": "completed", "error_detail": None,
            })
            paused += 1
            logger.info("Paused Instantly campaign at its configured end time (mapping_id=%s).", mapping["id"])
        except Exception as exc:
            # Keep status active so the next pass retries the idempotent provider pause.
            logger.error("Could not pause expired Instantly campaign (mapping_id=%s, error=%s).",
                         mapping.get("id"), type(exc).__name__)
    return paused
