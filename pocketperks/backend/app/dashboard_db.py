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


async def _recent_campaign_runs(client, base: str, headers: dict[str, str], limit: int = 12) -> tuple[list[dict], list[dict]]:
    response = await client.get(f"{base}/campaign_runs", params={
        "select": "id,campaign_id,batch_id,status,total_leads,processed,skipped,failed,created_at,finished_at",
        "order": "created_at.desc,id.desc", "limit": str(limit),
    }, headers=headers)
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load recent campaign activity.")
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        return [], []
    campaign_ids = sorted({str(row["campaign_id"]) for row in rows if row.get("campaign_id")})
    campaign_rows = []
    if campaign_ids:
        campaign_response = await client.get(f"{base}/campaigns", params={
            "select": "id,name", "id": f"in.({','.join(campaign_ids)})",
        }, headers=headers)
        if not campaign_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load campaign names.")
        campaign_rows = campaign_response.json()
    names = {str(row["id"]): row.get("name") or "Campaign" for row in campaign_rows}
    options = [{
        "run_id": str(row["id"]), "campaign_id": row["campaign_id"],
        "campaign_name": names.get(str(row.get("campaign_id")), "Campaign"),
        "status": row["status"], "total_leads": row["total_leads"],
        "created_at": row["created_at"],
    } for row in rows]
    return rows, options


async def _campaign_run_detail(client, base: str, headers: dict[str, str], run: dict,
                               campaign_name: str | None = None) -> dict:
    run_id = str(run["id"])
    if campaign_name is None:
        campaign_response = await client.get(f"{base}/campaigns", params={
            "select": "name", "id": f"eq.{run['campaign_id']}", "limit": "1",
        }, headers=headers)
        if not campaign_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load the campaign name.")
        campaign_rows = campaign_response.json()
        campaign_name = campaign_rows[0].get("name") if campaign_rows else "Campaign"
    queued, sent, sent_test, failed, pending_review = await asyncio.gather(
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.queued"}),
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.sent"}),
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.sent_test"}),
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.failed"}),
        _count(client, base, headers, "personalized_emails", {"run_id": f"eq.{run_id}", "status": "eq.pending_review"}),
    )
    return {
        "run_id": run_id, "campaign_id": run["campaign_id"], "batch_id": run["batch_id"],
        "campaign_name": campaign_name or "Campaign",
        "status": run["status"], "total_leads": run["total_leads"],
        "processed": run["processed"], "skipped": run["skipped"], "failed_leads": run["failed"],
        "created_at": run["created_at"], "finished_at": run.get("finished_at"),
        "queued_emails": queued, "sent_emails": sent + sent_test,
        "failed_emails": failed, "pending_review_emails": pending_review,
    }


async def _latest_campaign_run(client, base: str, headers: dict[str, str]) -> tuple[dict | None, list[dict]]:
    rows, options = await _recent_campaign_runs(client, base, headers)
    if not rows:
        return None, options
    return await _campaign_run_detail(client, base, headers, rows[0], options[0]["campaign_name"]), options


async def get_dashboard_run_detail(settings: Settings, run_id: str) -> dict:
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{base}/campaign_runs", params={
            "select": "id,campaign_id,batch_id,status,total_leads,processed,skipped,failed,created_at,finished_at",
            "id": f"eq.{run_id}", "limit": "1",
        }, headers=headers)
        if not response.is_success:
            raise HTTPException(status_code=503, detail="Could not load this campaign activity.")
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Campaign activity was not found.")
        return await _campaign_run_detail(client, base, headers, rows[0])


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
    latest_run, recent_runs = totals[12]
    return {"total_leads": totals[0], "total_campaigns": totals[1], "approved_campaigns": totals[2],
            "pending_approvals": totals[3], "queued_not_sent": totals[4], "active_runs": totals[5],
            "total_replies": totals[6], "unread_replies": totals[7],
            "instantly_campaigns": totals[8], "instantly_leads_synced": totals[9],
            "instantly_active_campaigns": totals[10],
            "instantly_auto_launch_enabled": settings.instantly_auto_launch and settings.send_mode == "live",
            "activity": totals[11], "latest_run": latest_run, "recent_runs": recent_runs}
