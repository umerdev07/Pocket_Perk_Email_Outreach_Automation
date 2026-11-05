"""Reconcile Instantly campaign sends into PocketPerks' authoritative email rows."""
import asyncio
import re
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.instantly_sent_sync_db import load_queued_campaigns, mark_email_sent, provider_sent_timestamp
from app.integrations.instantly_client import list_campaign_sent_emails

_SYNC_LOCK = asyncio.Lock()
MAX_CAMPAIGNS_PER_CYCLE = 5


def _step_number(row: dict) -> int:
    relation = row.get("campaign_steps")
    if isinstance(relation, list):
        relation = relation[0] if relation else {}
    try:
        return int((relation or {}).get("step_number") or 0)
    except (TypeError, ValueError):
        return 0


def _normalized_subject(value: object) -> str:
    subject = " ".join(str(value or "").casefold().split())
    return re.sub(r"^(?:(?:re|fw|fwd):\s*)+", "", subject)


async def sync_sent_email_statuses(settings: Settings, *, campaign_offset: int = 0) -> dict[str, int]:
    """Mark rows sent only after matching a sent campaign email from Instantly.

    Uses Instantly's read-only emails endpoint. This path cannot launch campaigns
    or send messages. Five campaign reads per minute leave room under the
    provider's tighter 20-requests/minute email-list limit for reply polling and retries.
    """
    if type(campaign_offset) is not int or campaign_offset < 0:
        raise ValueError("Campaign offset must be a non-negative integer.")
    async with _SYNC_LOCK:
        campaigns = await load_queued_campaigns(settings)
        if not campaigns:
            return {"campaigns_scanned": 0, "campaigns_available": 0, "marked_sent": 0}

        start = campaign_offset % len(campaigns)
        ordered_campaigns = campaigns[start:] + campaigns[:start]
        selected = ordered_campaigns[:MAX_CAMPAIGNS_PER_CYCLE]
        marked = 0
        for campaign in selected:
            queued = campaign["queued_emails"]
            earliest = min((row.get("created_at") for row in queued if isinstance(row.get("created_at"), str)), default=None)
            if earliest:
                try:
                    since = datetime.fromisoformat(earliest.replace("Z", "+00:00")) - timedelta(days=1)
                    min_created = since.astimezone(UTC).isoformat()
                except ValueError:
                    min_created = None
            else:
                min_created = None

            sent_items = await list_campaign_sent_emails(
                str(campaign["instantly_campaign_id"]), settings=settings,
                limit=100, min_timestamp_created=min_created,
            )
            queued_by_recipient: dict[str, list[dict]] = {}
            queued_by_provider_lead: dict[str, list[dict]] = {}
            for row in queued:
                lead = row.get("leads")
                if isinstance(lead, list):
                    lead = lead[0] if lead else {}
                address = str((lead or {}).get("email") or "").strip().casefold()
                if address:
                    queued_by_recipient.setdefault(address, []).append(row)
                provider_lead_id = row.get("instantly_lead_id")
                if provider_lead_id:
                    queued_by_provider_lead.setdefault(str(provider_lead_id), []).append(row)
            for rows in queued_by_recipient.values():
                rows.sort(key=_step_number)
            for rows in queued_by_provider_lead.values():
                rows.sort(key=_step_number)

            sent_items.sort(key=lambda item: str(item.get("timestamp_email") or item.get("timestamp_created") or ""))
            for item in sent_items:
                if str(item.get("campaign_id") or "") != str(campaign["instantly_campaign_id"]):
                    continue
                address = str(item.get("lead") or "").strip().casefold()
                provider_lead_id = str(item.get("lead_id") or "")
                candidates = queued_by_provider_lead.get(provider_lead_id) or queued_by_recipient.get(address, [])
                if not candidates:
                    continue
                subject = _normalized_subject(item.get("subject"))
                match = next((row for row in candidates if _normalized_subject(row.get("subject")) == subject), None)
                if not match:
                    continue
                provider_id = item.get("id")
                if not isinstance(provider_id, str) or not provider_id.strip():
                    continue
                if await mark_email_sent(settings, str(match["id"]), provider_id, provider_sent_timestamp(item)):
                    marked += 1
                    candidates.remove(match)

        return {"campaigns_scanned": len(selected), "campaigns_available": len(campaigns), "marked_sent": marked}
