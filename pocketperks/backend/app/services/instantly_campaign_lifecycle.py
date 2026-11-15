"""Enforce exact campaign end timestamps for auto-launched Instantly campaigns."""
import asyncio
import logging
from datetime import UTC, datetime

from app.config import Settings
from app.campaign_control_lock import campaign_control_lock
from app.instantly_sync_db import (load_active_campaign_windows, load_scheduled_campaign_windows,
                                   load_campaigns_for_provider_reconciliation,
                                   record_provider_campaign_check, update_campaign_mapping)
from app.integrations.instantly_client import activate_campaign, get_campaign, pause_campaign

logger = logging.getLogger(__name__)


async def reconcile_deleted_provider_campaigns(settings: Settings, pool=None, *, run_id=None, force=False, limit=50) -> int:
    """Mark provider campaigns deleted in Instantly without discarding local run history."""
    if not settings.instantly_api_key:
        return 0
    mappings = await load_campaigns_for_provider_reconciliation(settings, run_id=run_id, limit=limit, force=force, pool=pool)
    semaphore = asyncio.Semaphore(5)

    async def check(mapping: dict) -> int:
        async with semaphore:
            try:
                provider = await get_campaign(str(mapping["instantly_campaign_id"]), settings)
                await record_provider_campaign_check(settings, mapping["id"], deleted=provider is None, pool=pool)
                if provider is None:
                    logger.info("Instantly campaign deletion confirmed (mapping_id=%s).", mapping["id"])
                    return 1
            except Exception as exc:
                logger.warning("Could not reconcile Instantly campaign (mapping_id=%s, error=%s).",
                               mapping.get("id"), type(exc).__name__)
        return 0

    return sum(await asyncio.gather(*(check(mapping) for mapping in mappings)))


async def reconcile_completed_campaigns(settings: Settings, pool=None) -> int:
    """Mirror provider-confirmed completed status for active local mappings."""
    mappings = await load_active_campaign_windows(settings,pool=pool)
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
                    },pool=pool)
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


async def pause_campaigns_past_end_time(settings: Settings, pool=None) -> int:
    """Pause expired campaigns and persist their completed mapping state."""
    now = datetime.now(UTC)
    mappings = await load_active_campaign_windows(settings,pool=pool)
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
            },pool=pool)
            paused += 1
            logger.info("Paused Instantly campaign at its configured end time (mapping_id=%s).", mapping["id"])
        except Exception as exc:
            # Keep status active so the next pass retries the idempotent provider pause.
            logger.error("Could not pause expired Instantly campaign (mapping_id=%s, error=%s).",
                         mapping.get("id"), type(exc).__name__)
    return paused


async def activate_scheduled_campaigns(settings: Settings, pool=None) -> int:
    """Launch campaigns with assigned inboxes once their configured start time arrives."""
    if not settings.instantly_auto_launch or settings.send_mode != "live":
        return 0
    now = datetime.now(UTC)
    mappings = await load_scheduled_campaign_windows(settings,pool=pool)
    activated = 0
    for mapping in mappings:
        # The user pause/resume API uses the same lock. Re-read state only after
        # acquiring it so a campaign paused at its start time cannot be launched
        # from a stale scheduler snapshot.
        async with campaign_control_lock(pool, str(mapping["id"])) as conn:
            current = await conn.fetchrow("""select m.status,m.instantly_campaign_id,m.sender_account_emails,
                    c.start_at,coalesce(r.schedule_end_at,c.end_at) end_at
                from public.instantly_campaign_mappings m
                join public.campaign_runs r on r.id=m.run_id
                join public.campaigns c on c.id=r.campaign_id
                where m.id=$1::uuid""", str(mapping["id"]))
            if not current or current["status"] != "scheduled":
                continue
            try:
                start_at = current["start_at"]
                end_at = current["end_at"]
                if start_at.tzinfo is None or end_at.tzinfo is None:
                    raise ValueError("missing timezone")
            except (AttributeError, TypeError, ValueError):
                logger.error("Scheduled Instantly campaign has invalid timestamps (mapping_id=%s).", mapping.get("id"))
                continue
            if not current["sender_account_emails"]:
                continue
            if end_at.astimezone(UTC) <= now:
                await conn.execute("""update public.instantly_campaign_mappings
                    set status='failed',error_detail='Campaign window ended before its scheduled activation.',updated_at=now()
                    where id=$1::uuid""", str(mapping["id"]))
                continue
            if start_at.astimezone(UTC) > now:
                continue
            await conn.execute("""update public.instantly_campaign_mappings
                set status='launching',error_detail='Scheduled activation submitted; confirmation is pending.',updated_at=now()
                where id=$1::uuid""", str(mapping["id"]))
            try:
                await activate_campaign(str(current["instantly_campaign_id"]), settings)
                await conn.execute("""update public.instantly_campaign_mappings
                    set status='active',error_detail=null,updated_at=now() where id=$1::uuid""",
                    str(mapping["id"]))
                activated += 1
            except Exception as exc:
                # Unknown outcomes remain launching so the worker never blindly repeats activation.
                if not getattr(exc, "outcome_unknown", False):
                    await conn.execute("""update public.instantly_campaign_mappings
                        set status='failed',error_detail=$2,updated_at=now() where id=$1::uuid""",
                        str(mapping["id"]), f"Scheduled Instantly activation failed: {type(exc).__name__}")
                logger.error("Could not activate Instantly campaign (mapping_id=%s, error=%s).",
                             mapping.get("id"), type(exc).__name__)
    return activated
