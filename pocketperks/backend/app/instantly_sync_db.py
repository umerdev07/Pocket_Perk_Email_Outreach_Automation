"""Supabase persistence for Instantly provider object mappings."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

from app.config import Settings

DB_BATCH_SIZE = 500


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for Instantly sync.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def _checked(response, detail: str) -> list[dict]:
    if not response.is_success:
        raise HTTPException(status_code=503, detail=detail)
    try:
        rows = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=detail) from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise HTTPException(status_code=503, detail=detail)
    return rows


async def create_campaign_mapping(settings: Settings, run_id: str,
                                 sender_emails: list[str] | None = None) -> dict:
    """Create or retrieve the one provider mapping row for a PocketPerks run."""
    base = _base(settings)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{base}/instantly_campaign_mappings",
            params={"on_conflict": "run_id"},
            headers=_headers(settings, "resolution=ignore-duplicates,return=representation"),
            json={"run_id": run_id, **({"sender_account_emails": sender_emails}
                                        if sender_emails is not None else {})},
        )
        rows = await _checked(response, "Could not create Instantly campaign mapping.")
        if rows:
            return rows[0]
        response = await client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "id,run_id,instantly_campaign_id,status,error_detail,sender_account_emails,created_at,updated_at",
            "run_id": f"eq.{run_id}", "limit": "1",
        }, headers=_headers(settings))
        rows = await _checked(response, "Could not load Instantly campaign mapping.")
    if not rows:
        raise HTTPException(status_code=503, detail="Instantly campaign mapping was not returned.")
    return rows[0]


async def update_campaign_mapping(settings: Settings, mapping_id: str, values: dict) -> dict:
    allowed = {"instantly_campaign_id", "status", "error_detail", "sender_account_emails"}
    if not values or set(values) - allowed:
        raise ValueError("Only provider campaign ID, status, and safe error detail can be updated.")
    values = {**values, "updated_at": datetime.now(UTC).isoformat()}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(
            f"{_base(settings)}/instantly_campaign_mappings",
            params={"id": f"eq.{mapping_id}"}, headers=_headers(settings, "return=representation"),
            json=values,
        )
    rows = await _checked(response, "Could not update Instantly campaign mapping.")
    if not rows:
        raise HTTPException(status_code=404, detail="Instantly campaign mapping not found.")
    return rows[0]


async def get_campaign_mapping_for_run(settings: Settings, run_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/instantly_campaign_mappings", params={
            "select": "id,run_id,instantly_campaign_id,status,error_detail,sender_account_emails",
            "run_id": f"eq.{run_id}", "limit": "1",
        }, headers=_headers(settings))
    rows = await _checked(response, "Could not load the Instantly campaign mapping.")
    return rows[0] if rows else None


async def load_scheduled_campaign_windows(settings: Settings) -> list[dict]:
    """Load scheduled mappings with their app-owned start/end timestamps."""
    base = _base(settings)
    async with httpx.AsyncClient(timeout=25) as client:
        mappings = await _checked(await client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "id,run_id,instantly_campaign_id,sender_account_emails",
            "status": "eq.scheduled", "limit": "100",
        }, headers=_headers(settings)), "Could not load scheduled Instantly campaigns.")
        results = []
        for mapping in mappings:
            runs = await _checked(await client.get(f"{base}/campaign_runs", params={
                "select": "campaign_id,schedule_end_at,campaigns(start_at,end_at)",
                "id": f"eq.{mapping['run_id']}", "limit": "1",
            }, headers=_headers(settings)), "Could not load a scheduled campaign window.")
            if not runs:
                continue
            campaign = runs[0].get("campaigns") or {}
            if isinstance(campaign, list):
                campaign = campaign[0] if campaign else {}
            results.append({**mapping, "start_at": campaign.get("start_at"),
                            "end_at": runs[0].get("schedule_end_at") or campaign.get("end_at")})
    return results


async def save_lead_mappings(settings: Settings, mapping_id: str, rows: list[dict]) -> int:
    """Upsert local lead/provider IDs in bounded chunks for restart-safe sync."""
    if not rows:
        return 0
    allowed_statuses = {"pending", "synced", "skipped", "failed", "unknown"}
    for row in rows:
        if (not isinstance(row, dict) or not row.get("lead_id")
                or row.get("status") not in allowed_statuses):
            raise ValueError("Each lead mapping needs a lead ID and valid sync status.")
        if row.get("status") == "synced" and not row.get("instantly_lead_id"):
            raise ValueError("A synced lead mapping requires an Instantly lead ID.")

    base = _base(settings)
    saved = 0
    updated_at = datetime.now(UTC).isoformat()
    async with httpx.AsyncClient(timeout=30) as client:
        for offset in range(0, len(rows), DB_BATCH_SIZE):
            chunk = [{"campaign_mapping_id": mapping_id, "updated_at": updated_at,
                      **row} for row in rows[offset:offset + DB_BATCH_SIZE]]
            response = await client.post(
                f"{base}/instantly_lead_mappings",
                params={"on_conflict": "campaign_mapping_id,lead_id"},
                headers=_headers(settings, "resolution=merge-duplicates,return=representation"),
                json=chunk,
            )
            returned = await _checked(response, "Could not save Instantly lead mappings.")
            if len(returned) != len(chunk):
                raise HTTPException(status_code=503, detail="Supabase did not confirm every Instantly lead mapping.")
            saved += len(returned)
    return saved


async def get_lead_mapping_statuses(settings: Settings, mapping_id: str,
                                    lead_ids: list[str]) -> dict[str, dict]:
    """Load local sync state before a retry; unknown provider outcomes are never resent."""
    if not lead_ids:
        return {}
    found: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=20) as client:
        for offset in range(0, len(lead_ids), DB_BATCH_SIZE):
            chunk = lead_ids[offset:offset + DB_BATCH_SIZE]
            response = await client.get(f"{_base(settings)}/instantly_lead_mappings", params={
                "select": "lead_id,instantly_lead_id,status,error_detail",
                "campaign_mapping_id": f"eq.{mapping_id}",
                "lead_id": f"in.({','.join(chunk)})",
            }, headers=_headers(settings))
            rows = await _checked(response, "Could not load Instantly lead sync state.")
            found.update({str(row["lead_id"]): row for row in rows if row.get("lead_id")})
    return found


async def load_sync_run_context(settings: Settings, run_id: str) -> dict:
    """Load the approved campaign and run guard before any provider write."""
    base = _base(settings)
    async with httpx.AsyncClient(timeout=25) as client:
        runs = await _checked(await client.get(f"{base}/campaign_runs", params={
            "select": "id,campaign_id,batch_id,status,sender_account_emails,schedule_end_at", "id": f"eq.{run_id}", "limit": "1",
        }, headers=_headers(settings)), "Could not load the campaign run for Instantly sync.")
        if not runs:
            raise HTTPException(status_code=404, detail="Campaign run not found.")
        run = runs[0]
        if run.get("status") != "completed":
            raise HTTPException(status_code=409, detail="Personalization must finish before Instantly sync.")
        sender_emails = run.get("sender_account_emails")
        if not isinstance(sender_emails, list) or not sender_emails:
            raise HTTPException(status_code=409, detail="Select at least one Instantly sending inbox before starting this campaign.")
        campaigns = await _checked(await client.get(f"{base}/campaigns", params={
            "select": "id,name,total_emails,start_date,end_date,start_at,end_at,timezone,daily_send_start,daily_send_end,sending_days,status,campaign_steps(id,step_number,delay_days,delay_minutes,generic_drafts(id,status))",
            "id": f"eq.{run['campaign_id']}", "limit": "1",
        }, headers=_headers(settings)), "Could not load the approved campaign for Instantly sync.")
    if not campaigns:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    campaign = campaigns[0]
    if campaign.get("status") != "approved":
        raise HTTPException(status_code=409, detail="Campaign must remain approved before Instantly sync.")
    # An automatic extension belongs to this run only; leave the approved
    # campaign and any other runs using it unchanged.
    if run.get("schedule_end_at"):
        campaign["end_at"] = run["schedule_end_at"]
        try:
            campaign_zone = ZoneInfo(str(campaign.get("timezone") or "UTC"))
            campaign["end_date"] = datetime.fromisoformat(
                str(run["schedule_end_at"]).replace("Z", "+00:00")
            ).astimezone(campaign_zone).date().isoformat()
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=503, detail="The campaign run has an invalid adjusted end time.") from exc
    steps = []
    for step in campaign.get("campaign_steps") or []:
        draft = step.get("generic_drafts")
        if isinstance(draft, list):
            draft = draft[0] if draft else None
        steps.append({**step, "draft": draft})
    steps.sort(key=lambda step: step.get("step_number", 0))
    if len(steps) != campaign.get("total_emails") or any(
            not isinstance(step.get("draft"), dict) or step["draft"].get("status") != "approved"
            for step in steps):
        raise HTTPException(status_code=409, detail="Every campaign step needs an approved draft before Instantly sync.")
    campaign["steps"] = steps
    return {"run": run, "campaign": campaign}


async def load_sync_lead_page(settings: Settings, run_id: str, batch_id: str,
                              *, offset: int, limit: int) -> list[dict]:
    """Read a bounded lead page and its generated emails from Supabase."""
    base = _base(settings)
    async with httpx.AsyncClient(timeout=30) as client:
        leads = await _checked(await client.get(f"{base}/leads", params={
            "select": "id,business_name,email,phone,website,business_category,city,verification_result,quality_flags",
            "batch_id": f"eq.{batch_id}", "order": "id.asc", "limit": str(limit), "offset": str(offset),
        }, headers=_headers(settings)), "Could not load leads for Instantly sync.")
        if not leads:
            return []
        lead_ids = [str(lead["id"]) for lead in leads]
        emails = []
        for page_offset in range(0, len(lead_ids), 100):
            lead_chunk = lead_ids[page_offset:page_offset + 100]
            email_rows = await _checked(await client.get(f"{base}/personalized_emails", params={
                "select": "id,lead_id,step_id,subject,body,status,risk_level,campaign_step:campaign_steps(step_number)",
                "run_id": f"eq.{run_id}", "lead_id": f"in.({','.join(lead_chunk)})",
                "order": "lead_id.asc,step_id.asc", "limit": "1000",
            }, headers=_headers(settings)), "Could not load reviewed personalized emails for Instantly sync.")
            emails.extend(email_rows)
    emails_by_lead: dict[str, list[dict]] = {}
    for email in emails:
        step = email.pop("campaign_step", None)
        if isinstance(step, list):
            step = step[0] if step else None
        email["step_number"] = step.get("step_number") if isinstance(step, dict) else None
        emails_by_lead.setdefault(str(email.get("lead_id")), []).append(email)
    for lead in leads:
        lead["personalized_steps"] = emails_by_lead.get(str(lead["id"]), [])
    return leads


async def load_active_campaign_windows(settings: Settings, *, limit: int = 100) -> list[dict]:
    """Load active Instantly mappings and their PocketPerks campaign end times."""
    base = _base(settings)
    async with httpx.AsyncClient(timeout=25) as client:
        mappings = await _checked(await client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "id,run_id,instantly_campaign_id,status", "status": "in.(active,launching)",
            "instantly_campaign_id": "not.is.null", "order": "updated_at.asc", "limit": str(limit),
        }, headers=_headers(settings)), "Could not load active Instantly campaign mappings.")
        run_ids = [str(row["run_id"]) for row in mappings if row.get("run_id")]
        runs_by_id: dict[str, dict] = {}
        for offset in range(0, len(run_ids), 100):
            chunk = run_ids[offset:offset + 100]
            runs = await _checked(await client.get(f"{base}/campaign_runs", params={
                "select": "id,schedule_end_at,campaigns(end_at)", "id": f"in.({','.join(chunk)})",
                "limit": str(len(chunk)),
            }, headers=_headers(settings)), "Could not load campaign end times for auto-launch.")
            runs_by_id.update({str(row["id"]): row for row in runs if row.get("id")})
    result = []
    for mapping in mappings:
        run = runs_by_id.get(str(mapping.get("run_id"))) or {}
        campaign = run.get("campaigns")
        if isinstance(campaign, list):
            campaign = campaign[0] if campaign else None
        end_at = (run.get("schedule_end_at") if isinstance(run.get("schedule_end_at"), str)
                  else campaign.get("end_at") if isinstance(campaign, dict) else None)
        if isinstance(end_at, str):
            result.append({**mapping, "end_at": end_at})
    return result


async def load_active_runs_with_pending_leads(settings: Settings, *, limit: int = 100) -> list[str]:
    """Find campaigns whose leads became eligible after human review.

    Includes active campaigns with delayed approval notifications, campaigns
    left in syncing after a worker interruption, campaigns explicitly waiting
    for review, older no-eligible-leads failures, and campaigns awaiting launch.
    """
    base = _base(settings)
    async with httpx.AsyncClient(timeout=25) as client:
        waiting = await _checked(await client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "id,run_id,status", "status": "in.(active,awaiting_review)",
            "instantly_campaign_id": "not.is.null", "order": "updated_at.asc", "limit": str(limit),
        }, headers=_headers(settings)), "Could not load pending-review Instantly campaigns.")
        interrupted = await _checked(await client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "id,run_id,status", "status": "eq.syncing_leads",
            "instantly_campaign_id": "not.is.null", "order": "updated_at.asc", "limit": str(limit),
        }, headers=_headers(settings)), "Could not load interrupted Instantly lead syncs.")
        launch_ready = []
        if settings.instantly_auto_launch and settings.send_mode == "live":
            launch_ready = await _checked(await client.get(f"{base}/instantly_campaign_mappings", params={
                "select": "id,run_id,status", "status": "eq.leads_synced",
                "instantly_campaign_id": "not.is.null", "order": "updated_at.asc", "limit": str(limit),
            }, headers=_headers(settings)), "Could not load Instantly campaigns awaiting scheduled launch.")
        legacy_no_eligible = await _checked(await client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "id,run_id", "status": "eq.failed", "error_detail": "eq.No eligible leads synced to Instantly.",
            "instantly_campaign_id": "not.is.null", "order": "updated_at.asc", "limit": str(limit),
        }, headers=_headers(settings)), "Could not load legacy Instantly campaigns awaiting review.")
        mappings = waiting + interrupted + launch_ready + legacy_no_eligible
        mapping_ids = [str(row["id"]) for row in mappings if row.get("id")]
        if not mapping_ids:
            return []
        pending = await _checked(await client.get(f"{base}/instantly_lead_mappings", params={
            "select": "campaign_mapping_id", "campaign_mapping_id": f"in.({','.join(mapping_ids)})",
            "status": "in.(pending,unknown)", "limit": "5000",
        }, headers=_headers(settings)), "Could not load active Instantly campaigns for recovery.")
    pending_ids = {str(row["campaign_mapping_id"]) for row in pending if row.get("campaign_mapping_id")}
    # A leads_synced mapping has no pending lead rows, but still needs a
    # lifecycle pass when auto-launch is enabled in live mode.
    launch_ready_ids = {str(row["id"]) for row in launch_ready if row.get("id")}
    interrupted_ids = {str(row["id"]) for row in interrupted if row.get("id")}
    return list(dict.fromkeys(str(row["run_id"]) for row in mappings
                               if row.get("run_id") and (str(row.get("id")) in pending_ids
                                   or str(row.get("id")) in launch_ready_ids
                                   or str(row.get("id")) in interrupted_ids)))
