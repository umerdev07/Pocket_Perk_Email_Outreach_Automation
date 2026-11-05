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


async def _latest_campaign_run(client, base: str, headers: dict[str, str]) -> dict | None:
    response = await client.get(f"{base}/campaign_runs", params={
        "select": "id,campaign_id,batch_id,status,total_leads,processed,skipped,failed,created_at,finished_at",
        "order": "created_at.desc", "limit": "1",
    }, headers=headers)
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load the latest campaign run.")
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        return None
    run = rows[0]
    run_id = str(run["id"])
    campaign_response = await client.get(f"{base}/campaigns", params={
        "select": "name", "id": f"eq.{run['campaign_id']}", "limit": "1",
    }, headers=headers)
    if not campaign_response.is_success:
        raise HTTPException(status_code=503, detail="Could not load the latest campaign name.")
    campaign_rows = campaign_response.json()
    queued, sent, sent_test, failed, pending_review = await asyncio.gather(
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.queued"}),
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.sent"}),
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.sent_test"}),
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.failed"}),
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.pending_review"}),
    )
    return {
        "run_id": run_id, "campaign_id": run["campaign_id"], "batch_id": run["batch_id"],
        "campaign_name": campaign_rows[0].get("name") if campaign_rows else "Campaign",
        "status": run["status"], "total_leads": run["total_leads"],
        "processed": run["processed"], "skipped": run["skipped"], "failed_leads": run["failed"],
        "created_at": run["created_at"], "finished_at": run.get("finished_at"),
        "queued_emails": queued, "sent_emails": sent + sent_test,
        "failed_emails": failed, "pending_review_emails": pending_review,
    }


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
            _count(client, base, headers, "instantly_campaign_mappings", {"status": "eq.active"}),
            _activity(client, base, headers, activity_days),
            _latest_campaign_run(client, base, headers),
        )
    return {"total_leads": totals[0], "total_campaigns": totals[1], "approved_campaigns": totals[2],
            "pending_approvals": totals[3], "queued_not_sent": totals[4], "active_runs": totals[5],
            "total_replies": totals[6], "unread_replies": totals[7],
            "instantly_campaigns": totals[8], "instantly_leads_synced": totals[9],
            "instantly_active_campaigns": totals[10],
            "instantly_auto_launch_enabled": settings.instantly_auto_launch and settings.send_mode == "live",
            "activity": totals[11], "latest_run": totals[12]}
