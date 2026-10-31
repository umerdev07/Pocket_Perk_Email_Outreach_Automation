"""Supabase persistence for the human approval queue."""
import httpx
from fastapi import HTTPException

from app.config import Settings


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for approvals.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def _get(client, url: str, params: dict, headers: dict) -> list[dict]:
    response = await client.get(url, params=params, headers=headers)
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load approval data.")
    return response.json()


async def list_pending_approvals(settings: Settings, *, limit: int, offset: int) -> dict:
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base}/personalized_emails", params={
            "select": "id,lead_id,campaign_id,step_id,subject,body,status,risk_level,risk_reason,created_at",
            "status": "eq.pending_review", "order": "created_at.asc,id.asc",
            "limit": str(limit), "offset": str(offset),
        }, headers={**headers, "Prefer": "count=exact"})
        if not response.is_success:
            raise HTTPException(status_code=503, detail="Could not load the human approval queue.")
        rows = response.json()
        try:
            total = int(response.headers.get("content-range", "").rsplit("/", 1)[1])
        except (IndexError, ValueError):
            total = offset + len(rows)

        if rows:
            lead_ids = sorted({row["lead_id"] for row in rows})
            campaign_ids = sorted({row["campaign_id"] for row in rows})
            step_ids = sorted({row["step_id"] for row in rows})
            leads = await _get(client, f"{base}/leads", {
                "select": "id,business_name,email,business_category,city",
                "id": f"in.({','.join(lead_ids)})",
            }, headers)
            campaigns = await _get(client, f"{base}/campaigns", {
                "select": "id,name,instructions", "id": f"in.({','.join(campaign_ids)})",
            }, headers)
            steps = await _get(client, f"{base}/campaign_steps", {
                "select": "id,step_number,context", "id": f"in.({','.join(step_ids)})",
            }, headers)
            lead_map = {row["id"]: row for row in leads}
            campaign_map = {row["id"]: row for row in campaigns}
            step_map = {row["id"]: row for row in steps}
            for row in rows:
                row["lead"] = lead_map.get(row["lead_id"], {})
                row["campaign"] = campaign_map.get(row["campaign_id"], {})
                row["step"] = step_map.get(row["step_id"], {})

    return {"items": rows, "total": total, "limit": limit, "offset": offset,
            "has_more": offset + len(rows) < total}


async def list_review_history(settings: Settings, *, limit: int, offset: int) -> list[dict]:
    """Return database-triggered audit entries for automated checks and human decisions."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/email_review_history", params={
            "select": "id,email_id,action,status,risk_level,risk_reason,subject,business_name,recipient_email,campaign_name,step_number,created_at",
            "order": "created_at.desc,id.desc", "limit": str(limit), "offset": str(offset),
        }, headers=_headers(settings))
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load email review history. Apply the latest Supabase migration.")
    return response.json()


async def get_email_review_context(settings: Settings, email_id: str) -> dict | None:
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=20) as client:
        emails = await _get(client, f"{base}/personalized_emails", {
            "select": "id,lead_id,campaign_id,step_id,subject,body,status,risk_level,risk_reason",
            "id": f"eq.{email_id}", "limit": "1",
        }, headers)
        if not emails:
            return None
        email = emails[0]
        leads = await _get(client, f"{base}/leads", {
            "select": "id,business_name,email,business_category,city,phone,website,full_address",
            "id": f"eq.{email['lead_id']}", "limit": "1",
        }, headers)
        drafts = await _get(client, f"{base}/generic_drafts", {
            "select": "id,step_id,subject,body,status", "step_id": f"eq.{email['step_id']}", "limit": "1",
        }, headers)
        campaigns = await _get(client, f"{base}/campaigns", {
            "select": "id,name,instructions", "id": f"eq.{email['campaign_id']}", "limit": "1",
        }, headers)
        steps = await _get(client, f"{base}/campaign_steps", {
            "select": "id,step_number,context", "id": f"eq.{email['step_id']}", "limit": "1",
        }, headers)
    if not leads or not drafts or not campaigns or not steps:
        raise HTTPException(status_code=409, detail="Lead, campaign, step, or approved draft context is missing for this email.")
    return {"email": email, "lead": leads[0], "draft": drafts[0],
            "campaign": campaigns[0], "step": steps[0]}


async def update_review_email(settings: Settings, email_id: str, expected_status: str, values: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(f"{_base(settings)}/personalized_emails", params={
            "id": f"eq.{email_id}", "status": f"eq.{expected_status}",
        }, headers=_headers(settings, "return=representation"), json=values)
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not update the personalized email.")
    rows = response.json()
    if not rows:
        raise HTTPException(status_code=409, detail="This email changed while you were reviewing it. Reload the queue.")
    return rows[0]
