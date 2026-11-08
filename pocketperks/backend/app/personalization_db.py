"""Supabase access for background campaign personalization."""
import httpx
from fastapi import HTTPException

from app.config import Settings


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for personalization.")
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
        raise HTTPException(status_code=503, detail="Could not load campaign personalization data.")
    return response.json()


async def load_run_context(settings: Settings, run_id: str) -> dict:
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=20) as client:
        runs = await _get(client, f"{base}/campaign_runs", {
            "select": "id,campaign_id,batch_id,total_leads", "id": f"eq.{run_id}", "limit": "1",
        }, headers)
        if not runs:
            raise HTTPException(status_code=404, detail="Campaign run not found.")
        run = runs[0]
        campaigns = await _get(client, f"{base}/campaigns", {
            "select": "id,name,instructions,status,campaign_steps(id,step_number,context,trigger_type,generic_drafts(id,subject,body,status))",
            "id": f"eq.{run['campaign_id']}", "limit": "1",
        }, headers)
        if not campaigns:
            raise HTTPException(status_code=404, detail="Campaign not found for this run.")
        campaign = campaigns[0]
        steps = campaign.get("campaign_steps") or []
        normalized_steps = []
        for step in steps:
            relation = step.get("generic_drafts")
            if isinstance(relation, list):
                draft = relation[0] if relation else None
            else:
                draft = relation if isinstance(relation, dict) else None
            if not draft or draft.get("status") != "approved":
                raise HTTPException(status_code=409, detail="Every campaign step must have an approved draft.")
            normalized_steps.append({**step, "draft": draft})
        if not normalized_steps:
            raise HTTPException(status_code=409, detail="Campaign has no approved email steps.")

    return {"run": run, "campaign": {**campaign, "steps": sorted(normalized_steps, key=lambda x: x["step_number"])} }


async def load_lead_page(settings: Settings, batch_id: str, *, offset: int, limit: int) -> list[dict]:
    """Fetch one stable, ordered page so large batches aren't held in memory."""
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(client, f"{_base(settings)}/leads", {
            "select": "id,business_name,first_name,business_category,email,city,website,verification_result,quality_flags", "batch_id": f"eq.{batch_id}",
            "order": "id.asc", "limit": str(limit), "offset": str(offset),
        }, _headers(settings))


async def get_run_personalized_emails(settings: Settings, run_id: str, *, limit: int, offset: int) -> dict:
    """Return one page of saved personalized emails plus its lead and step labels."""
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=30) as client:
        runs = await _get(client, f"{base}/campaign_runs", {
            "select": "id", "id": f"eq.{run_id}", "limit": "1",
        }, headers)
        if not runs:
            raise HTTPException(status_code=404, detail="Campaign run not found.")

        response = await client.get(f"{base}/personalized_emails", params={
            "select": "id,lead_id,step_id,subject,body,status,manual_send_state,risk_level,risk_reason,created_at",
            "run_id": f"eq.{run_id}", "order": "created_at.desc,id.desc",
            "limit": str(limit), "offset": str(offset),
        }, headers={**headers, "Prefer": "count=exact"})
        if not response.is_success:
            raise HTTPException(status_code=503, detail="Could not load personalized emails for this run.")
        rows = response.json()
        content_range = response.headers.get("content-range", "")
        try:
            total = int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            total = offset + len(rows)

        if rows:
            lead_ids = sorted({row["lead_id"] for row in rows})
            step_ids = sorted({row["step_id"] for row in rows})
            leads = await _get(client, f"{base}/leads", {
                "select": "id,business_name,first_name,email", "id": f"in.({','.join(lead_ids)})",
            }, headers)
            steps = await _get(client, f"{base}/campaign_steps", {
                "select": "id,step_number,context,trigger_type", "id": f"in.({','.join(step_ids)})",
            }, headers)
            lead_by_id = {lead["id"]: lead for lead in leads}
            step_by_id = {step["id"]: step for step in steps}
            for row in rows:
                row["lead"] = lead_by_id.get(row["lead_id"], {})
                row["step"] = step_by_id.get(row["step_id"], {})

    return {"items": rows, "total": total, "limit": limit, "offset": offset,
            "has_more": offset + len(rows) < total}


async def get_run_lead_personalized_emails(settings: Settings, run_id: str, lead_id: str) -> dict:
    """Return every generated step for one lead, with its lead and step context."""
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=30) as client:
        rows = await _get(client, f"{base}/personalized_emails", {
            "select": "id,lead_id,step_id,subject,body,status,manual_send_state,risk_level,risk_reason,created_at",
            "run_id": f"eq.{run_id}", "lead_id": f"eq.{lead_id}", "order": "created_at.asc,id.asc",
            "limit": "10",
        }, headers)
        if not rows:
            raise HTTPException(status_code=404, detail="No personalized emails found for this lead in this run.")
        leads = await _get(client, f"{base}/leads", {
            "select": "id,business_name,first_name,email,business_category,city,website",
            "id": f"eq.{lead_id}", "limit": "1",
        }, headers)
        step_ids = sorted({row["step_id"] for row in rows})
        steps = await _get(client, f"{base}/campaign_steps", {
            "select": "id,step_number,context,trigger_type", "id": f"in.({','.join(step_ids)})",
        }, headers)
    if not leads:
        raise HTTPException(status_code=404, detail="Lead not found.")
    step_by_id = {step["id"]: step for step in steps}
    for row in rows:
        row["lead"] = leads[0]
        row["step"] = step_by_id.get(row["step_id"], {})
    rows.sort(key=lambda row: row.get("step", {}).get("step_number", 0))
    return {"lead": leads[0], "items": rows}


async def save_personalized_email(settings: Settings, email: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{_base(settings)}/personalized_emails",
            params={"on_conflict": "run_id,lead_id,step_id"},
            headers=_headers(settings, "resolution=ignore-duplicates,return=representation"), json=email)
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not save a personalized email.")
    rows = response.json()
    return rows[0] if rows else {}


async def get_existing_personalization_keys(settings: Settings, run_id: str,
                                            lead_ids: list[str]) -> set[tuple[str, str]]:
    """Load saved lead/step pairs for one small chunk to avoid repeat AI calls after a restart."""
    if not lead_ids or not settings.supabase_url or not settings.supabase_service_key:
        return set()
    # Keep this query explicit: the bounded lead page contains at most one work chunk.
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/personalized_emails", params={
            "select": "lead_id,step_id", "run_id": f"eq.{run_id}",
            "lead_id": f"in.({','.join(lead_ids)})", "limit": "1000",
        }, headers=_headers(settings))
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not check saved personalized emails for this run.")
    return {(row["lead_id"], row["step_id"]) for row in response.json()}


async def update_run_progress(settings: Settings, run_id: str, *, processed: int, skipped: int,
                              failed: int, skip_log: list[dict] | None, status: str, finished: bool = False,
                              error_detail: str | None = None, failure_log: list[dict] | None = None) -> None:
    payload = {"processed": processed, "skipped": skipped, "failed": failed, "status": status}
    if skip_log is not None:
        payload["skip_log"] = skip_log
    if error_detail is not None:
        payload["error_detail"] = error_detail[:1000]
    if failure_log is not None:
        payload["failure_log"] = failure_log[:500]
    if finished:
        from datetime import UTC, datetime
        payload["finished_at"] = datetime.now(UTC).isoformat()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(f"{_base(settings)}/campaign_runs", params={"id": f"eq.{run_id}"},
            headers=_headers(settings), json=payload)
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not update campaign run progress.")
