from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.integrations.instantly_client import InstantlyError, list_sending_accounts
from app.instantly_sender_service import is_sending_account_ready
from app.instantly_sync_db import load_active_sender_usage

router = APIRouter(prefix="/api/instantly", tags=["Instantly"])


@router.get("/accounts")
async def instantly_accounts(settings: Settings = Depends(get_settings)):
    try:
        accounts = await list_sending_accounts(settings)
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [{**account, "sending_ready": is_sending_account_ready(account)} for account in accounts]


@router.get("/capacity")
async def instantly_capacity(sender_emails: list[str] = Query(default=[]),
                             settings: Settings = Depends(get_settings)):
    """Return configurable daily lead target and active campaign overlap.

    The provider API exposes configured per-account limits, while live daily
    usage remains authoritative in the provider dashboard. This endpoint does
    not claim those limits are unused or reserve provider capacity.
    """
    try:
        usage = await load_active_sender_usage(settings)
    except HTTPException:
        raise
    selected = {email.strip().lower() for email in sender_emails if email.strip()}
    active_by_sender = usage["by_sender"]
    overlaps = {email: active_by_sender.get(email, []) for email in sorted(selected)}
    overlapping_runs = {campaign["run_id"] for campaigns in overlaps.values() for campaign in campaigns
                        if campaign.get("run_id")}
    return {
        "new_leads_per_day_target": settings.outreach_new_leads_per_day,
        "active_campaign_count": usage["active_campaign_count"],
        "active_campaigns_using_selected_senders": len(overlapping_runs),
        "capacity_is_configured_maximum": True,
        "live_daily_usage_available": False,
    }
