"""Enforce exact campaign end timestamps for auto-launched Instantly campaigns."""
import asyncio
import logging
from datetime import UTC, datetime

from app.config import Settings
from app.instantly_sync_db import (load_active_campaign_windows, load_scheduled_campaign_windows,
                                   update_campaign_mapping)
from app.integrations.instantly_client import activate_campaign, get_campaign, pause_campaign

logger = logging.getLogger(__name__)


async def reconcile_completed_campaigns(settings: Settings) -> int:
    """Mirror provider-confirmed completed status for active local mappings."""
    mappings = await load_active_campaign_windows(settings)
    reconciled = 0
    semaphore = asyncio.Semaphore(5)

    async def reconcile(mapping: dict) -> int:
        campaign_id = mapping.get("instantly_campaign_id")
        if not campaign_id:
            return 0
        async with semaphore:
            try:
                provider_campaign = await get_campaign(str(campaign_id), settings)
                # Instantly API v2 campaign status 3 means Completed. Do not infer
                # completion from sends/replies locally; only persist provider truth.
                if provider_campaign and provider_campaign.get("status") in (3, "3"):
                    await update_campaign_mapping(settings, str(mapping["id"]), {
                        "status": "completed", "error_detail": None,
                    })
                    logger.info("Reconciled completed Instantly campaign (mapping_id=%s).", mapping["id"])
                    return 1
            except Exception as exc:
                # A transient provider failure must not hide an active campaign or
                # block scheduled activation/end-time enforcement.
                logger.warning("Could not reconcile Instantly campaign status (mapping_id=%s, error=%s).",
                               mapping.get("id"), type(exc).__name__)
        return 0

    reconciled = sum(await asyncio.gather(*(reconcile(mapping) for mapping in mappings)))
    return reconciled


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


async def activate_scheduled_campaigns(settings: Settings) -> int:
    """Launch campaigns with assigned inboxes once their configured start time arrives."""
    if not settings.instantly_auto_launch or settings.send_mode != "live":
        return 0
    now = datetime.now(UTC)
    mappings = await load_scheduled_campaign_windows(settings)
    activated = 0
    for mapping in mappings:
        try:
            start_at = datetime.fromisoformat(str(mapping["start_at"]).replace("Z", "+00:00"))
            end_at = datetime.fromisoformat(str(mapping["end_at"]).replace("Z", "+00:00"))
            if start_at.tzinfo is None or end_at.tzinfo is None:
                raise ValueError("missing timezone")
        except (KeyError, TypeError, ValueError):
            logger.error("Scheduled Instantly campaign has invalid timestamps (mapping_id=%s).", mapping.get("id"))
            continue
        if not mapping.get("sender_account_emails"):
            continue
        if end_at.astimezone(UTC) <= now:
            await update_campaign_mapping(settings, str(mapping["id"]), {
                "status": "failed", "error_detail": "Campaign window ended before its scheduled activation.",
            })
            continue
        if start_at.astimezone(UTC) > now:
            continue
        await update_campaign_mapping(settings, str(mapping["id"]), {
            "status": "launching", "error_detail": "Scheduled activation submitted; confirmation is pending.",
        })
        try:
            await activate_campaign(str(mapping["instantly_campaign_id"]), settings)
            await update_campaign_mapping(settings, str(mapping["id"]), {
                "status": "active", "error_detail": None,
            })
            activated += 1
        except Exception as exc:
            # Unknown outcomes remain launching so the worker never blindly repeats activation.
            if not getattr(exc, "outcome_unknown", False):
                await update_campaign_mapping(settings, str(mapping["id"]), {
                    "status": "failed", "error_detail": f"Scheduled Instantly activation failed: {type(exc).__name__}",
                })
            logger.error("Could not activate scheduled Instantly campaign (mapping_id=%s, error=%s).",
                         mapping.get("id"), type(exc).__name__)
    return activated
