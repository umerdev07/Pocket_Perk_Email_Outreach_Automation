"""Database operations for starting a campaign run."""
import asyncio

import httpx
from fastapi import HTTPException

from app.config import Settings


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for campaign runs.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def start_campaign_run(settings: Settings, campaign_id: str, batch_id: str) -> dict:
    """Validate both records and create a run; no lead processing happens here."""
    base = _base(settings)
    headers = _headers(settings)
    async with httpx.AsyncClient(timeout=15) as client:
        campaign_response = await client.get(f"{base}/campaigns", params={
            "select": "id,status", "id": f"eq.{campaign_id}", "limit": "1",
        }, headers=headers)
        if not campaign_response.is_success:
            raise HTTPException(status_code=503, detail="Could not verify the campaign.")
        campaigns = campaign_response.json()
        if not campaigns:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        if campaigns[0].get("status") != "approved":
            raise HTTPException(status_code=409, detail="Campaign must be approved before it can be started.")

        batch_response = await client.get(f"{base}/lead_batches", params={
            "select": "id,imported_count", "id": f"eq.{batch_id}", "limit": "1",
        }, headers=headers)
        if not batch_response.is_success:
            raise HTTPException(status_code=503, detail="Could not verify the lead batch.")
        batches = batch_response.json()
        if not batches:
            raise HTTPException(status_code=404, detail="Lead batch not found.")
        total_leads = int(batches[0]["imported_count"])

        run_response = await client.post(f"{base}/campaign_runs", headers=_headers(settings, "return=representation"), json={
            "campaign_id": campaign_id,
            "batch_id": batch_id,
            "status": "running",
            "total_leads": total_leads,
        })
        if getattr(run_response, "status_code", None) == 409:
            raise HTTPException(status_code=409,
                                detail="This campaign is already processing this lead batch. Wait for it to finish.")
        if not run_response.is_success or not run_response.json():
            raise HTTPException(status_code=503, detail="Could not create the campaign run.")
    row = run_response.json()[0]
    return {"run_id": row["id"], "status": row["status"], "total_leads": row["total_leads"]}


async def get_campaign_run(settings: Settings, run_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_base(settings)}/campaign_runs", params={
            "select": "id,campaign_id,batch_id,status,total_leads,processed,skipped,failed,error_detail,failure_log,created_at,finished_at",
            "id": f"eq.{run_id}", "limit": "1",
        }, headers=_headers(settings))
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load campaign run progress.")
    rows = response.json()
    if not rows:
        return None
    row = rows[0]
    return {"run_id": row["id"], "campaign_id": row["campaign_id"], "batch_id": row["batch_id"],
            "status": row["status"], "total_leads": row["total_leads"], "processed": row["processed"],
            "skipped": row["skipped"], "failed": row["failed"], "created_at": row["created_at"],
            "finished_at": row["finished_at"], "error_detail": row.get("error_detail"),
            "failure_log": row.get("failure_log") or []}


async def get_campaign_run_instantly_status(settings: Settings, run_id: str) -> dict:
    """Return provider sync/launch state for the run without inferring a send."""
    base = _base(settings)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "id,instantly_campaign_id,status,error_detail,sender_account_emails,updated_at",
            "run_id": f"eq.{run_id}", "limit": "1",
        }, headers=_headers(settings))
        if not response.is_success:
            raise HTTPException(status_code=503, detail="Could not load Instantly sync status.")
        rows = response.json()
        if not isinstance(rows, list):
            raise HTTPException(status_code=503, detail="Instantly sync status response was invalid.")
        if not rows:
            return {"run_id": run_id, "status": "waiting", "instantly_campaign_id": None,
                    "synced_leads": 0, "pending_leads": 0, "failed_leads": 0, "skipped_leads": 0,
                    "error_detail": None, "sender_account_emails": []}
        mapping = rows[0]
        mapping_id = str(mapping["id"])
        counts = await asyncio.gather(*[
            _count_run_lead_mappings(client, base, settings, mapping_id, status)
            for status in ("synced", "pending", "failed", "skipped", "unknown")
        ])
    return {"run_id": run_id, "status": mapping.get("status", "pending"),
            "instantly_campaign_id": mapping.get("instantly_campaign_id"),
            "synced_leads": counts[0], "pending_leads": counts[1], "failed_leads": counts[2],
            "skipped_leads": counts[3], "unknown_leads": counts[4],
            "error_detail": mapping.get("error_detail"),
            "sender_account_emails": mapping.get("sender_account_emails") or [],
            "updated_at": mapping.get("updated_at")}


async def _count_run_lead_mappings(client, base: str, settings: Settings,
                                   mapping_id: str, status: str) -> int:
    response = await client.get(f"{base}/instantly_lead_mappings", params={
        "select": "id", "campaign_mapping_id": f"eq.{mapping_id}",
        "status": f"eq.{status}", "limit": "1",
    }, headers={**_headers(settings), "Prefer": "count=exact"})
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load Instantly lead sync counts.")
    try:
        return int(response.headers["content-range"].rsplit("/", 1)[1])
    except (KeyError, IndexError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Database did not return exact Instantly lead counts.") from exc
