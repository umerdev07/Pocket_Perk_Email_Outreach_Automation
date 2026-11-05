"""Supabase persistence for replies imported from Instantly Unibox."""
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException

from app.config import Settings


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for lead replies.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _rows(response: httpx.Response, message: str) -> list[dict]:
    if not response.is_success:
        raise HTTPException(status_code=503, detail=message)
    try:
        result = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=message) from exc
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise HTTPException(status_code=503, detail=message)
    return result


async def list_replies(settings: Settings, *, limit: int = 100, offset: int = 0) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/replies", params={
            "select": "id,lead_id,campaign_id,from_email,subject,body,instantly_id,received_at,status,created_at,leads(business_name,email),campaigns(name)",
            "order": "received_at.desc", "limit": limit, "offset": offset,
        }, headers=_headers(settings))
    return _rows(response, "Could not load lead replies.")


async def save_received_replies(settings: Settings, emails: list[dict]) -> int:
    """Save only messages tied to our imported leads or mapped campaigns."""
    if not emails:
        return 0
    lead_emails = sorted({str(item.get("lead") or item.get("from_address_email") or "").strip().lower()
                          for item in emails
                          if isinstance(item.get("lead") or item.get("from_address_email"), str)
                          and (item.get("lead") or item.get("from_address_email")).strip()})
    campaign_ids = sorted({str(item.get("campaign_id")) for item in emails if item.get("campaign_id")})
    base, headers = _base(settings), _headers(settings)
    leads_by_email: dict[str, dict] = {}
    campaign_by_provider_id: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=25) as client:
        for offset in range(0, len(lead_emails), 100):
            chunk = lead_emails[offset:offset + 100]
            rows = _rows(await client.get(f"{base}/leads", params={
                "select": "id,email", "email": f"in.({','.join(chunk)})", "limit": len(chunk),
            }, headers=headers), "Could not match incoming replies to imported leads.")
            leads_by_email.update({str(row["email"]).lower(): row for row in rows if row.get("email")})
        for offset in range(0, len(campaign_ids), 100):
            chunk = campaign_ids[offset:offset + 100]
            rows = _rows(await client.get(f"{base}/instantly_campaign_mappings", params={
                "select": "instantly_campaign_id,campaign_runs(campaign_id)",
                "instantly_campaign_id": f"in.({','.join(chunk)})", "limit": len(chunk),
            }, headers=headers), "Could not match incoming replies to PocketPerks campaigns.")
            for row in rows:
                relation = row.get("campaign_runs")
                if isinstance(relation, dict) and row.get("instantly_campaign_id") and relation.get("campaign_id"):
                    campaign_by_provider_id[str(row["instantly_campaign_id"])] = str(relation["campaign_id"])

        pending = []
        for item in emails:
            provider_id = item.get("id")
            sender = item.get("from_address_email")
            lead_email = str(item.get("lead") or sender or "").strip().lower()
            lead = leads_by_email.get(lead_email)
            if not isinstance(provider_id, str) or not provider_id or not isinstance(sender, str) or not sender.strip():
                continue
            campaign_id = campaign_by_provider_id.get(str(item.get("campaign_id") or ""))
            if not lead and not campaign_id:
                continue
            body_obj = item.get("body") if isinstance(item.get("body"), dict) else {}
            body = body_obj.get("text") or item.get("content_preview") or "(Reply body unavailable in provider response.)"
            if not isinstance(body, str):
                body = str(body)
            received_at = item.get("timestamp_email") or item.get("timestamp_created")
            if not isinstance(received_at, str):
                received_at = datetime.now(UTC).isoformat()
            pending.append({
                "lead_id": lead.get("id") if lead else None,
                "campaign_id": campaign_id,
                "from_email": sender.strip().lower(),
                "subject": str(item.get("subject") or "(no subject)")[:500],
                "body": body[:20000],
                "instantly_id": provider_id,
                "received_at": received_at,
                "status": "new" if item.get("is_unread") else "read",
            })
        inserted = 0
        for offset in range(0, len(pending), 100):
            chunk = pending[offset:offset + 100]
            response = await client.post(f"{base}/replies", params={"on_conflict": "instantly_id"},
                                         headers=_headers(settings, "resolution=ignore-duplicates,return=representation"),
                                         json=chunk)
            inserted += len(_rows(response, "Could not save replies to the PocketPerks workspace."))
    return inserted


async def mark_reply_read(settings: Settings, reply_id: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(f"{_base(settings)}/replies", params={"id": f"eq.{reply_id}"},
                                      headers=_headers(settings, "return=representation"), json={"status": "read"})
    rows = _rows(response, "Could not update this reply.")
    if not rows:
        raise HTTPException(status_code=404, detail="Reply not found.")
    return rows[0]


async def get_reply_sync_state(settings: Settings) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/instantly_reply_sync_state", params={
            "select": "last_checked_at,sync_from,starting_after", "id": "eq.true", "limit": "1",
        }, headers=_headers(settings))
    rows = _rows(response, "Could not load the Instantly reply sync cursor. Apply migration 017 and retry.")
    if not rows:
        raise HTTPException(status_code=503, detail="Instantly reply sync cursor is missing. Apply migration 017.")
    return rows[0]


async def update_reply_sync_state(settings: Settings, *, last_checked_at: str | None = None,
                                  sync_from: str | None = None,
                                  starting_after: str | None = None) -> None:
    values = {"sync_from": sync_from, "starting_after": starting_after,
              "updated_at": datetime.now(UTC).isoformat()}
    if last_checked_at is not None:
        values["last_checked_at"] = last_checked_at
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(f"{_base(settings)}/instantly_reply_sync_state",
                                      params={"id": "eq.true"},
                                      headers=_headers(settings, "return=representation"), json=values)
    rows = _rows(response, "Could not update the Instantly reply sync cursor.")
    if not rows:
        raise HTTPException(status_code=503, detail="Instantly reply sync cursor was not updated.")
