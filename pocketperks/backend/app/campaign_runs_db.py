"""Database operations for starting a campaign run."""
import asyncio
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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


async def start_campaign_run(settings: Settings, campaign_id: str, batch_id: str,
                             sender_emails: list[str] | None = None,
                             sender_accounts: list[dict] | None = None) -> dict:
    """Validate both records and create a run; no lead processing happens here."""
    base = _base(settings)
    headers = _headers(settings)
    async with httpx.AsyncClient(timeout=15) as client:
        campaign_response = await client.get(f"{base}/campaigns", params={
            "select": "id,status,start_at,end_at,timezone,daily_send_start,daily_send_end,sending_days,campaign_steps(step_number,delay_days,delay_minutes,trigger_type)",
            "id": f"eq.{campaign_id}", "limit": "1",
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

        schedule_end_at = None
        schedule_adjustment = None
        if sender_accounts:
            estimate = _campaign_window_estimate(campaigns[0], total_leads, sender_accounts)
            if estimate and estimate.get("too_long"):
                raise HTTPException(
                    status_code=422,
                    detail=("This batch would need more than two years at the selected inbox limits. "
                            "Split the batch or select additional active inboxes before starting."),
                )
            if estimate and estimate["recommended_end_at"]:
                schedule_end_at = estimate["recommended_end_at"]
                schedule_adjustment = {
                    "previous_end_at": campaigns[0]["end_at"],
                    "new_end_at": schedule_end_at,
                    "timezone": str(campaigns[0].get("timezone") or "UTC"),
                    "buffer_sending_windows": 1,
                }

        run_response = await client.post(f"{base}/campaign_runs", headers=_headers(settings, "return=representation"), json={
            "campaign_id": campaign_id,
            "batch_id": batch_id,
            "status": "running",
            "total_leads": total_leads,
            **({"schedule_end_at": schedule_end_at} if schedule_end_at else {}),
            **({"sender_account_emails": sender_emails} if sender_emails is not None else {}),
        })
        if getattr(run_response, "status_code", None) == 409:
            raise HTTPException(status_code=409,
                                detail="This campaign is already processing this lead batch. Wait for it to finish.")
        if not run_response.is_success or not run_response.json():
            raise HTTPException(status_code=503, detail="Could not create the campaign run.")
    row = run_response.json()[0]
    return {"run_id": row["id"], "status": row["status"], "total_leads": row["total_leads"],
            **({"schedule_adjustment": schedule_adjustment} if schedule_adjustment else {})}


def _campaign_window_estimate(campaign: dict, total_leads: int,
                              sender_accounts: list[dict]) -> dict | None:
    """Conservative preflight from the configured daily window and mailbox limits."""
    try:
        start = datetime.fromisoformat(str(campaign["start_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(campaign["end_at"]).replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            return None
        zone = ZoneInfo(str(campaign.get("timezone") or "UTC"))
        local_start, local_end = start.astimezone(zone), end.astimezone(zone)
        daily_start = str(campaign.get("daily_send_start") or local_start.strftime("%H:%M"))[:5]
        daily_end = str(campaign.get("daily_send_end") or local_end.strftime("%H:%M"))[:5]
        start_hour, start_min = (int(part) for part in daily_start.split(":"))
        end_hour, end_min = (int(part) for part in daily_end.split(":"))
        start_minute = start_hour * 60 + start_min
        end_minute = end_hour * 60 + end_min
        window_minutes = end_minute - start_minute
        if window_minutes <= 0:
            return None
        configured_days = campaign.get("sending_days") or [1, 2, 3, 4, 5]
        allowed_weekdays = {(int(day) - 1) % 7 for day in configured_days}
        steps = sorted(campaign.get("campaign_steps") or [], key=lambda row: row.get("step_number", 0))
        steps = [step for step in steps if step.get("trigger_type", "scheduled") != "after_conversation"]
        if not steps:
            return None
        followup_days = sum(math.ceil(max(0, int(step.get("delay_minutes") if step.get("delay_minutes") is not None
                                                   else (step.get("delay_days") or 0) * 1440)) / 1440)
                            for step in steps[1:])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    capacity_per_window = 0
    for account in sender_accounts:
        try:
            daily_limit = int(float(account.get("daily_limit")))
            sending_gap = float(account.get("sending_gap") or 0)
        except (TypeError, ValueError):
            continue
        if daily_limit <= 0:
            continue
        gap_capacity = math.floor(window_minutes / sending_gap) if sending_gap > 0 else daily_limit
        capacity_per_window += max(1, min(daily_limit, gap_capacity))
    if capacity_per_window <= 0:
        return None

    total_messages = total_leads * len(steps)
    sending_windows = max(1, math.ceil(total_messages / capacity_per_window))
    # Instantly may keep provider-side work queued beyond a theoretical capacity
    # estimate. Reserve one additional allowed sending window so the stop date
    # does not coincide with the last expected send window.
    required_sending_windows = sending_windows + 1
    required_days = required_sending_windows + followup_days
    candidate_end = local_end
    while True:
        calendar_days = (candidate_end.date() - local_start.date()).days + 1
        sending_days = sum(
            (local_start.date() + timedelta(days=offset)).weekday() in allowed_weekdays
            for offset in range(max(0, calendar_days))
        )
        if calendar_days >= required_days and sending_days >= required_sending_windows:
            break
        candidate_end += timedelta(days=1)
        if (candidate_end.date() - local_start.date()).days > 730:
            return {"required_days": required_days, "available_days": 730,
                    "sending_windows": sending_windows, "recommended_end_at": None,
                    "too_short": True, "too_long": True}
    recommended_end_at = candidate_end.isoformat() if candidate_end > local_end else None
    available_calendar_days = (local_end.date() - local_start.date()).days + 1
    available_sending_days = sum(
        (local_start.date() + timedelta(days=offset)).weekday() in allowed_weekdays
        for offset in range(max(0, available_calendar_days))
    )
    return {
        "required_days": required_days,
        "available_days": available_calendar_days,
        "sending_windows": sending_windows,
        "recommended_end_at": recommended_end_at,
        "too_short": bool(recommended_end_at),
    }


async def get_campaign_run(settings: Settings, run_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_base(settings)}/campaign_runs", params={
            "select": "id,campaign_id,batch_id,status,total_leads,processed,skipped,failed,error_detail,failure_log,created_at,finished_at,schedule_end_at",
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
            "failure_log": row.get("failure_log") or [], "schedule_end_at": row.get("schedule_end_at")}


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
        run_response = await client.get(f"{base}/campaign_runs", params={
            "select": "total_leads", "id": f"eq.{run_id}", "limit": "1",
        }, headers=_headers(settings))
        if not run_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load campaign lead total for Instantly sync.")
        run_rows = run_response.json()
        expected_leads = int(run_rows[0].get("total_leads") or 0) if isinstance(run_rows, list) and run_rows else 0
        counts = await asyncio.gather(*[
            _count_run_lead_mappings(client, base, settings, mapping_id, status)
            for status in ("synced", "pending", "failed", "skipped", "unknown")
        ])
    mapping_status = mapping.get("status", "pending")
    error_detail = mapping.get("error_detail")
    if mapping_status == "failed" and error_detail == "No eligible leads synced to Instantly." and counts[1] > 0:
        # Older runs marked this normal human-review hold as failed. Present the
        # actual state while recovery reuses that same provider campaign.
        mapping_status = "awaiting_review"
        error_detail = "Waiting for personalized email review before lead sync."
    mapped_leads = sum(counts)
    unresolved_leads = counts[1] + counts[2] + counts[4]
    # A worker can stop after Instantly and Supabase confirmed every lead but
    # before it finalized the campaign mapping. Derive the truthful UI state;
    # the sync recovery worker will also reconcile the durable mapping state.
    fully_reconciled = (expected_leads > 0 and mapped_leads >= expected_leads
                        and unresolved_leads == 0 and counts[0] > 0)
    if mapping_status == "syncing_leads" and fully_reconciled:
        mapping_status = "leads_synced"
    if (mapping_status == "failed" and error_detail == "No eligible leads synced to Instantly."
            and fully_reconciled):
        mapping_status, error_detail = "leads_synced", None
    return {"run_id": run_id, "status": mapping_status,
            "instantly_campaign_id": mapping.get("instantly_campaign_id"),
            "synced_leads": counts[0], "pending_leads": counts[1], "failed_leads": counts[2],
            "skipped_leads": counts[3], "unknown_leads": counts[4],
            "error_detail": error_detail,
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
