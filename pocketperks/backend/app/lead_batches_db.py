"""Database actions for lead import batches."""
import httpx
from fastapi import HTTPException

from app.config import Settings
from app.instantly_sync_db import update_campaign_mapping
from app.integrations.instantly_client import InstantlyError, get_campaign


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for lead batches.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def _reconcile_batch_instantly_campaigns(settings: Settings, batch_id: str) -> None:
    """Refresh local active mapping statuses before an import cleanup decision.

    Instantly is authoritative for whether a mapped campaign still exists and
    can send. Any inconclusive provider read leaves the local guard untouched.
    """
    base, headers = _base(settings), _headers(settings)
    run_ids: list[str] = []
    run_offset = 0
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            runs_response = await client.get(f"{base}/campaign_runs", params={
                "select": "id", "batch_id": f"eq.{batch_id}",
                "order": "created_at.asc,id.asc", "limit": "500", "offset": str(run_offset),
            }, headers=headers)
            if not runs_response.is_success:
                raise HTTPException(status_code=503, detail="Could not verify linked Instantly campaigns.")
            runs = runs_response.json()
            if not isinstance(runs, list) or any(not isinstance(row, dict) for row in runs):
                raise HTTPException(status_code=503, detail="Could not verify linked Instantly campaigns.")
            run_ids.extend(str(row["id"]) for row in runs if row.get("id"))
            if len(runs) < 500:
                break
            run_offset += len(runs)
        if not run_ids:
            return
        for offset in range(0, len(run_ids), 100):
            chunk = run_ids[offset:offset + 100]
            mappings_response = await client.get(f"{base}/instantly_campaign_mappings", params={
                "select": "id,run_id,instantly_campaign_id,status",
                "run_id": f"in.({','.join(chunk)})",
                "status": "in.(launching,scheduled,active)",
                "instantly_campaign_id": "not.is.null",
                "limit": "100",
            }, headers=headers)
            if not mappings_response.is_success:
                raise HTTPException(status_code=503, detail="Could not verify linked Instantly campaigns.")
            mappings = mappings_response.json()
            if not isinstance(mappings, list) or any(not isinstance(row, dict) for row in mappings):
                raise HTTPException(status_code=503, detail="Could not verify linked Instantly campaigns.")
            for mapping in mappings:
                campaign_id = mapping.get("instantly_campaign_id")
                if not isinstance(campaign_id, str) or not campaign_id:
                    continue
                try:
                    provider_campaign = await get_campaign(campaign_id, settings)
                except InstantlyError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="Instantly status could not be verified; cleanup was not changed. Check campaigns:read access and retry.",
                    ) from exc
                if provider_campaign is None:
                    await update_campaign_mapping(settings, str(mapping["id"]), {
                        "status": "completed",
                        "error_detail": "Campaign no longer exists in Instantly; reconciled during import cleanup.",
                    })
                    continue
                raw_status = provider_campaign["status"]
                normalized = str(raw_status).strip().lower()
                provider_to_local = {
                    "0": "draft_created", "draft": "draft_created",
                    "1": "active", "active": "active", "running": "active",
                    "2": "paused", "paused": "paused",
                    "3": "completed", "completed": "completed",
                    # Instantly uses status 4 for running subsequences; keep the
                    # cleanup guard active while any sequence can still run.
                    "4": "active", "running_subsequences": "active",
                }
                local_status = provider_to_local.get(normalized)
                if local_status is None:
                    raise HTTPException(
                        status_code=503,
                        detail="Instantly returned an unfamiliar campaign status; cleanup was not changed.",
                    )
                if local_status != mapping.get("status"):
                    await update_campaign_mapping(settings, str(mapping["id"]), {
                        "status": local_status,
                        "error_detail": None,
                    })


async def get_lead_batch_details(settings: Settings, batch_id: str, *, view: str, limit: int, offset: int) -> dict:
    """Load one page of imported leads or one page from the batch reject log."""
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base}/lead_batches", params={
            "select": "id,file_name,total_rows,imported_count,duplicate_count,invalid_count,rejected_count,suppressed_count,reject_log,created_at",
            "id": f"eq.{batch_id}", "limit": "1",
        }, headers=headers)
        if not response.is_success:
            raise HTTPException(status_code=503, detail="Could not load import details.")
        batches = response.json()
        if not batches:
            raise HTTPException(status_code=404, detail="Lead batch not found.")
        batch = batches[0]
        if view == "imported":
            rows_response = await client.get(f"{base}/leads", params={
                "select": "id,business_name,first_name,business_category,email,phone,website,domain,city,full_address,email_source_url,verification_result,verification_tool,quality_flags",
                "batch_id": f"eq.{batch_id}", "order": "created_at.asc,id.asc",
                "limit": str(limit), "offset": str(offset),
            }, headers={**headers, "Prefer": "count=exact"})
            if not rows_response.is_success:
                raise HTTPException(status_code=503, detail="Could not load imported lead records.")
            try:
                total = int(rows_response.headers.get("content-range", "").rsplit("/", 1)[1])
            except (IndexError, ValueError):
                total = int(batch.get("imported_count") or 0)
            rows = rows_response.json()
        else:
            rows = batch.get("reject_log") or []
            total = len(rows)
            rows = rows[offset:offset + limit]
    return {"batch": {key: value for key, value in batch.items() if key != "reject_log"},
            "view": view, "rows": rows, "total": total, "limit": limit, "offset": offset,
            "has_more": offset + len(rows) < total}


async def get_batch_lead_personalized_emails(settings: Settings, batch_id: str, lead_id: str) -> dict:
    """Return all saved personalized emails for one imported lead across this batch's runs."""
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=30) as client:
        lead_response = await client.get(f"{base}/leads", params={
            "select": "id,batch_id,business_name,first_name,business_category,email,city,website",
            "id": f"eq.{lead_id}", "batch_id": f"eq.{batch_id}", "limit": "1",
        }, headers=headers)
        if not lead_response.is_success:
            raise HTTPException(status_code=503, detail="Could not verify this lead in the import batch.")
        leads = lead_response.json()
        if not leads:
            raise HTTPException(status_code=404, detail="Lead not found in this import batch.")
        runs_response = await client.get(f"{base}/campaign_runs", params={
            "select": "id,campaign_id,status,created_at", "batch_id": f"eq.{batch_id}",
            "order": "created_at.desc", "limit": "100",
        }, headers=headers)
        if not runs_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load campaign runs for this import.")
        runs = runs_response.json()
        if not runs:
            return {"lead": leads[0], "items": []}
        run_ids = [row["id"] for row in runs]
        email_response = await client.get(f"{base}/personalized_emails", params={
            "select": "id,lead_id,campaign_id,step_id,run_id,subject,body,status,risk_level,risk_reason,created_at",
            "lead_id": f"eq.{lead_id}", "run_id": f"in.({','.join(run_ids)})",
            "order": "created_at.desc,id.desc", "limit": "1000",
        }, headers=headers)
        if not email_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load personalized emails for this lead.")
        emails = email_response.json()
        if not emails:
            return {"lead": leads[0], "items": []}
        campaign_ids = sorted({row["campaign_id"] for row in emails})
        step_ids = sorted({row["step_id"] for row in emails})
        campaigns_response = await client.get(f"{base}/campaigns", params={
            "select": "id,name", "id": f"in.({','.join(campaign_ids)})",
        }, headers=headers)
        steps_response = await client.get(f"{base}/campaign_steps", params={
            "select": "id,step_number,context", "id": f"in.({','.join(step_ids)})",
        }, headers=headers)
        if not campaigns_response.is_success or not steps_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load campaign email context.")
    campaigns = {row["id"]: row for row in campaigns_response.json()}
    steps = {row["id"]: row for row in steps_response.json()}
    for item in emails:
        item["campaign"] = campaigns.get(item["campaign_id"], {})
        item["step"] = steps.get(item["step_id"], {})
    emails.sort(key=lambda item: (item.get("campaign", {}).get("name", ""),
                                  item.get("step", {}).get("step_number", 0),
                                  item.get("created_at", "")))
    return {"lead": leads[0], "items": emails}


async def delete_lead_batch(settings: Settings, batch_id: str) -> None:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for lead batches.")
    await _reconcile_batch_instantly_campaigns(settings, batch_id)
    key = settings.supabase_service_key
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/rpc/delete_lead_batch_and_related_data",
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"target_batch_id": batch_id},
        )
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not delete the lead batch.")
    result = response.json()
    if isinstance(result, list):
        result = result[0] if result else {}
    if result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="Lead batch not found.")
    if result.get("reason") == "active_run":
        raise HTTPException(status_code=409, detail="This batch has a campaign run in progress. Wait for it to finish before deleting.")
    if result.get("reason") == "active_provider_campaign":
        raise HTTPException(status_code=409, detail="An Instantly campaign linked to this batch is still active. Pause or stop it in Instantly before cleaning this activity.")
    if result.get("deleted") is not True:
        raise HTTPException(status_code=503, detail="Lead batch deletion could not be confirmed.")


async def preview_lead_batch_cleanup(settings: Settings, batch_id: str) -> dict:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for lead batches.")
    key = settings.supabase_service_key
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/rpc/preview_lead_batch_cleanup",
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"target_batch_id": batch_id},
        )
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not preview this activity cleanup.")
    result = response.json()
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict) or result.get("found") is not True:
        raise HTTPException(status_code=404, detail="Import batch not found.")
    if result.get("active_or_scheduled_instantly_campaign_count", 0) > 0:
        await _reconcile_batch_instantly_campaigns(settings, batch_id)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{settings.supabase_url.rstrip('/')}/rest/v1/rpc/preview_lead_batch_cleanup",
                headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"target_batch_id": batch_id},
            )
        if not response.is_success:
            raise HTTPException(status_code=503, detail="Could not refresh the activity cleanup preview.")
        result = response.json()
        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, dict) or result.get("found") is not True:
            raise HTTPException(status_code=404, detail="Import batch not found.")
    return result
