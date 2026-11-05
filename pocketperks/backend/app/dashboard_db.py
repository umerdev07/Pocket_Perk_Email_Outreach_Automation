"""Accurate aggregate counts used by the Import Leads dashboard."""
import asyncio

import httpx
from fastapi import HTTPException

from app.config import Settings


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for the dashboard.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings) -> dict[str, str]:
    key = settings.supabase_service_key
    return {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}


async def _count(client, base: str, headers: dict[str, str], table: str, filters: dict | None = None) -> int:
    params = {"select": "id", "limit": "1", **(filters or {})}
    response = await client.get(f"{base}/{table}", params=params, headers=headers)
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load dashboard totals.")
    try:
        return int(response.headers["content-range"].rsplit("/", 1)[1])
    except (KeyError, IndexError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Database did not return an exact dashboard count.") from exc


async def _activity(client, base: str, headers: dict[str, str], days: int) -> dict:
    response = await client.post(f"{base}/rpc/dashboard_activity", json={"p_days": days}, headers=headers)
    if not response.is_success:
        return {"days": [], "campaign_status": [], "email_status": [], "available": False}
    try:
        value = response.json()
    except (ValueError, TypeError) as exc:
        return {"days": [], "campaign_status": [], "email_status": [], "available": False}
    if not isinstance(value, dict):
        return {"days": [], "campaign_status": [], "email_status": [], "available": False}
    return value


async def get_dashboard_summary(settings: Settings, *, activity_days: int = 14) -> dict:
    if type(activity_days) is not int or not 7 <= activity_days <= 90:
        raise ValueError("Dashboard activity must be between 7 and 90 days.")
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=20) as client:
        totals = await asyncio.gather(
            _count(client, base, headers, "leads"),
            _count(client, base, headers, "campaigns"),
            _count(client, base, headers, "campaigns", {"status": "eq.approved"}),
            _count(client, base, headers, "personalized_emails", {"status": "eq.pending_review"}),
            _count(client, base, headers, "personalized_emails", {"status": "eq.queued"}),
            _count(client, base, headers, "campaign_runs", {"status": "eq.running"}),
            _count(client, base, headers, "replies"),
            _count(client, base, headers, "replies", {"status": "eq.new"}),
            _count(client, base, headers, "instantly_campaign_mappings", {"instantly_campaign_id": "not.is.null"}),
            _count(client, base, headers, "instantly_lead_mappings", {"status": "eq.synced"}),
            _activity(client, base, headers, activity_days),
        )
    return {"total_leads": totals[0], "total_campaigns": totals[1], "approved_campaigns": totals[2],
            "pending_approvals": totals[3], "queued_not_sent": totals[4], "active_runs": totals[5],
            "total_replies": totals[6], "unread_replies": totals[7],
            "instantly_campaigns": totals[8], "instantly_leads_synced": totals[9],
            "activity": totals[10]}
