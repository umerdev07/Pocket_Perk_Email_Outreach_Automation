"""Accurate aggregate counts used by the Import Leads dashboard."""
import asyncio

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.bounce_db import get_bounce_summary


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


async def _bounce_activity(settings: Settings) -> dict:
    try:
        return await get_bounce_summary(settings)
    except HTTPException:
        # A dashboard refresh remains usable during rollout before migration 034
        # is applied; the bounce panel then reports that details are unavailable.
        return {"available": False, "total": 0, "hard": 0, "soft": 0, "unknown": 0, "recent": []}


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
    run_ids = [str(row["id"]) for row in rows if row.get("id")]
    sync_response = await client.get(f"{base}/instantly_campaign_mappings", params={
        "select": "run_id,status,error_detail", "run_id": f"in.({','.join(run_ids)})",
        "limit": str(len(run_ids)),
    }, headers=headers)
    if not sync_response.is_success:
        raise HTTPException(status_code=503, detail="Could not load recent campaign sync states.")
    sync_rows = sync_response.json()
    sync_by_run = {str(row["run_id"]): row for row in sync_rows
                   if isinstance(row, dict) and row.get("run_id")}
    options = [{
        "run_id": str(row["id"]), "campaign_id": row["campaign_id"],
        "campaign_name": names.get(str(row.get("campaign_id")), "Campaign"),
        "status": row["status"], "total_leads": row["total_leads"],
        "created_at": row["created_at"],
        "sync_status": sync_by_run.get(str(row["id"]), {}).get("status", "not_started"),
        "sync_error": sync_by_run.get(str(row["id"]), {}).get("error_detail"),
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
    email_totals_response, sync_response = await asyncio.gather(
        client.post(f"{base}/rpc/dashboard_run_email_totals", json={"p_run_id": run_id}, headers=headers),
        client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "status,error_detail", "run_id": f"eq.{run_id}", "limit": "1",
        }, headers=headers),
    )
    if not email_totals_response.is_success:
        raise HTTPException(status_code=503, detail="Could not load campaign delivery totals.")
    if not sync_response.is_success:
        raise HTTPException(status_code=503, detail="Could not load campaign sync state.")
    try:
        email_totals = email_totals_response.json()
        sync_rows = sync_response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Campaign delivery details were invalid.") from exc
    if not isinstance(email_totals, dict) or not isinstance(sync_rows, list):
        raise HTTPException(status_code=503, detail="Campaign delivery details were invalid.")
    sync = sync_rows[0] if sync_rows else {}
    return {
        "run_id": run_id, "campaign_id": run["campaign_id"], "batch_id": run["batch_id"],
        "campaign_name": campaign_name or "Campaign",
        "status": run["status"], "total_leads": run["total_leads"],
        "processed": run["processed"], "skipped": run["skipped"], "failed_leads": run["failed"],
        "created_at": run["created_at"], "finished_at": run.get("finished_at"),
        "sync_status": sync.get("status", "not_started"), "sync_error": sync.get("error_detail"),
        "queued_emails": int(email_totals.get("queued", 0)),
        "not_added_emails": int(email_totals.get("not_added", 0)),
        "sent_emails": int(email_totals.get("sent", 0)),
        "failed_emails": int(email_totals.get("failed", 0)),
        "pending_review_emails": int(email_totals.get("pending_review", 0)),
        "bounced_emails": int(email_totals.get("bounced", 0)),
        "cancelled_after_bounce": int(email_totals.get("cancelled", 0)),
    }


async def _latest_campaign_run(client, base: str, headers: dict[str, str]) -> tuple[dict | None, list[dict]]:
    rows, options = await _recent_campaign_runs(client, base, headers)
    if not rows:
        return None, options
    return await _campaign_run_detail(client, base, headers, rows[0], options[0]["campaign_name"]), options


async def _recent_completed_campaigns(client, base: str, headers: dict[str, str], limit: int = 5) -> list[dict]:
    """Return names for recently completed provider campaigns."""
    mappings = await client.get(f"{base}/instantly_campaign_mappings", params={
        "select": "run_id,updated_at", "status": "eq.completed",
        "order": "updated_at.desc", "limit": str(limit),
    }, headers=headers)
    if not mappings.is_success:
        raise HTTPException(status_code=503, detail="Could not load completed campaign activity.")
    mapping_rows = mappings.json()
    run_ids = list(dict.fromkeys(str(row["run_id"]) for row in mapping_rows
                                 if isinstance(row, dict) and row.get("run_id")))
    if not run_ids:
        return []
    runs = await client.get(f"{base}/campaign_runs", params={
        "select": "id,campaign_id", "id": f"in.({','.join(run_ids)})",
        "limit": str(len(run_ids)),
    }, headers=headers)
    if not runs.is_success:
        raise HTTPException(status_code=503, detail="Could not load completed campaign names.")
    run_rows = runs.json()
    campaign_ids = list(dict.fromkeys(str(row["campaign_id"]) for row in run_rows
                                      if isinstance(row, dict) and row.get("campaign_id")))
    if not campaign_ids:
        return []
    campaigns = await client.get(f"{base}/campaigns", params={
        "select": "id,name", "id": f"in.({','.join(campaign_ids)})",
        "limit": str(len(campaign_ids)),
    }, headers=headers)
    if not campaigns.is_success:
        raise HTTPException(status_code=503, detail="Could not load completed campaign names.")
    names = {str(row["id"]): row.get("name") or "Campaign" for row in campaigns.json()
             if isinstance(row, dict) and row.get("id")}
    run_names = {str(row["id"]): names.get(str(row.get("campaign_id")), "Campaign")
                 for row in run_rows if isinstance(row, dict) and row.get("id")}
    return [{"campaign_name": run_names.get(str(row["run_id"]), "Campaign"),
             "completed_at": row.get("updated_at")}
            for row in mapping_rows if isinstance(row, dict) and row.get("run_id")]


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
            _count(client, base, headers, "instantly_campaign_mappings", {"status": "eq.completed"}),
            _activity(client, base, headers, activity_days),
            _latest_campaign_run(client, base, headers),
            _recent_completed_campaigns(client, base, headers),
            _bounce_activity(settings),
        )
    latest_run, recent_runs = totals[13]
    activity = totals[12]
    delivery_totals = activity.get("delivery_totals", {}) if isinstance(activity, dict) else {}
    queued_not_sent = int(delivery_totals.get("queued", 0))
    not_added_emails = int(delivery_totals.get("not_added", 0))
    return {"total_leads": totals[0], "total_campaigns": totals[1], "approved_campaigns": totals[2],
            "pending_approvals": totals[3], "queued_not_sent": queued_not_sent,
            "not_added_emails": not_added_emails, "active_runs": totals[5],
            "total_replies": totals[6], "unread_replies": totals[7],
            "instantly_campaigns": totals[8], "instantly_leads_synced": totals[9],
            "instantly_active_campaigns": totals[10], "instantly_completed_campaigns": totals[11],
            "instantly_auto_launch_enabled": settings.instantly_auto_launch and settings.send_mode == "live",
            "activity": activity, "latest_run": latest_run, "recent_runs": recent_runs,
            "recent_completed_campaigns": totals[14], "bounce_activity": totals[15]}
