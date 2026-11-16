from fastapi import APIRouter, Depends, HTTPException, Query, Request

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
async def instantly_capacity(request: Request, sender_emails: list[str] = Query(default=[]),
                             campaign_id: str | None = None,
                             settings: Settings = Depends(get_settings)):
    """Return configurable daily lead target and active campaign overlap.

    The provider exposes configured per-account limits, while current daily
    sent usage remains authoritative in its dashboard. Local reservations are
    conservative forecasts, not a live provider quota reservation.
    """
    try:
        usage = await load_active_sender_usage(settings)
    except HTTPException:
        raise
    selected = {email.strip().lower() for email in sender_emails if email.strip()}
    active_by_sender = usage["by_sender"]
    selected_by_sender = {email: active_by_sender.get(email, []) for email in sorted(selected)}
    target_start = target_end = None
    if campaign_id:
        pool = getattr(request.app.state, "postgres_pool", None)
        if pool is None:
            raise HTTPException(status_code=503, detail="PostgreSQL is temporarily unavailable.")
        async with pool.acquire() as conn:
            target = await conn.fetchrow("select start_at,end_at from public.campaigns where id=$1::uuid", campaign_id)
        if not target:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        target_start, target_end = target["start_at"], target["end_at"]
    def overlaps(campaign):
        start, end = campaign.get("start_at"), campaign.get("end_at")
        return (target_start is None or start is None or end is None
                or (start <= target_end and end >= target_start))
    # Include scheduled/active runs even when they use a different inbox:
    # this is a workspace-wide cap across the selected five-mailbox pool.
    all_campaigns = {item["run_id"]: item for campaigns in active_by_sender.values()
                     for item in campaigns if item.get("run_id") and overlaps(item)}
    reserved = sum(int(item.get("reserved_daily_emails") or 0) for item in all_campaigns.values())
    selected_campaigns = {item["run_id"]: item for campaigns in selected_by_sender.values()
                          for item in campaigns if item.get("run_id")
                          and item.get("capacity_state") == "reserved" and overlaps(item)}
    return {
        "new_leads_per_day_target": settings.outreach_new_leads_per_day,
        "daily_email_capacity": settings.outreach_daily_email_capacity,
        "reserved_daily_emails": reserved,
        "remaining_daily_emails": max(0, settings.outreach_daily_email_capacity - reserved),
        "queued_capacity_runs": usage["queued_capacity_runs"],
        "active_campaign_count": usage["active_campaign_count"],
        "active_campaigns_using_selected_senders": len(selected_campaigns),
        "overlapping_campaigns": list(selected_campaigns.values()),
        "capacity_is_configured_maximum": True,
        "live_daily_usage_available": False,
    }
