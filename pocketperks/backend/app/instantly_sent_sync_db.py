"""Read and safely reconcile local queued drafts with confirmed Instantly sends."""
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException

from app.config import Settings


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for sent-email sync.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _rows(response: httpx.Response, detail: str) -> list[dict]:
    if not response.is_success:
        raise HTTPException(status_code=503, detail=detail)
    try:
        value = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=detail) from exc
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise HTTPException(status_code=503, detail=detail)
    return value


async def load_queued_campaigns(settings: Settings) -> list[dict]:
    """Load only local runs that have a provider campaign and queued email rows."""
    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=25) as client:
        mappings = _rows(await client.get(f"{base}/instantly_campaign_mappings", params={
            "select": "id,run_id,instantly_campaign_id,status",
            "status": "in.(leads_synced,active)",
            "instantly_campaign_id": "not.is.null", "order": "created_at.asc", "limit": "1000",
        }, headers=headers), "Could not load mapped Instantly campaigns for sent status sync.")
        if not mappings:
            return []

        by_run = {str(row["run_id"]): row for row in mappings if row.get("run_id") and row.get("instantly_campaign_id")}
        grouped: dict[str, list[dict]] = {run_id: [] for run_id in by_run}
        run_ids = list(by_run)
        for offset in range(0, len(run_ids), 50):
            chunk = run_ids[offset:offset + 50]
            emails = _rows(await client.get(f"{base}/personalized_emails", params={
                "select": "id,run_id,campaign_id,lead_id,step_id,subject,created_at,leads(email),campaign_steps(step_number)",
                "run_id": f"in.({','.join(chunk)})", "status": "eq.queued",
                "order": "created_at.asc", "limit": "1000",
            }, headers=headers), "Could not load queued personalized emails for sent status sync.")
            for email in emails:
                run_id = str(email.get("run_id") or "")
                if run_id in grouped:
                    grouped[run_id].append(email)

        for run_id, mapping in by_run.items():
            if not grouped[run_id]:
                continue
            lead_mappings = _rows(await client.get(f"{base}/instantly_lead_mappings", params={
                "select": "lead_id,instantly_lead_id", "campaign_mapping_id": f"eq.{mapping['id']}",
                "status": "eq.synced", "limit": "1000",
            }, headers=headers), "Could not load synced Instantly lead IDs for status matching.")
            provider_ids = {str(row["lead_id"]): str(row["instantly_lead_id"])
                            for row in lead_mappings if row.get("lead_id") and row.get("instantly_lead_id")}
            for email in grouped[run_id]:
                email["instantly_lead_id"] = provider_ids.get(str(email.get("lead_id") or ""))

    return [{**by_run[run_id], "queued_emails": emails}
            for run_id, emails in grouped.items() if emails]


async def mark_email_sent(settings: Settings, email_id: str, provider_email_id: str,
                          sent_at: str) -> bool:
    """Transition queued to sent only if this row is still queued."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(f"{_base(settings)}/personalized_emails", params={
            "id": f"eq.{email_id}", "status": "eq.queued",
        }, headers=_headers(settings, "return=representation"), json={
            "status": "sent", "sent_at": sent_at, "instantly_ref": provider_email_id,
            "send_error": None,
        })
    return bool(_rows(response, "Could not update the confirmed sent-email status."))


def provider_sent_timestamp(item: dict) -> str:
    value = item.get("timestamp_email") or item.get("timestamp_created")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
        except ValueError:
            pass
    return datetime.now(UTC).isoformat()
