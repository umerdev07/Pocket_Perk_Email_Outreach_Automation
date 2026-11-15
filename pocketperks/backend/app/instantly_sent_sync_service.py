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


def _provider_recipient(item: dict) -> str:
    """Read the lead address across the fields returned by Instantly's email API."""
    for field in ("lead", "to_address_email_list"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            # The documented `lead` field is a single address. The recipient list
            # may contain several comma-separated addresses; use an exact address
            # only when it resolves to one recipient.
            addresses = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", value, re.IGNORECASE)
            if len(addresses) == 1:
                return addresses[0].casefold()
    return ""


async def sync_sent_email_statuses(settings: Settings, *, campaign_offset: int = 0, pool=None) -> dict[str, int]:
    """Mark rows sent only after matching a sent campaign email from Instantly.

    Uses Instantly's read-only emails endpoint. This path cannot launch campaigns
    or send messages. Five campaign reads per minute leave room under the
    provider's tighter 20-requests/minute email-list limit for reply polling and retries.
    """
    if type(campaign_offset) is not int or campaign_offset < 0:
        raise ValueError("Campaign offset must be a non-negative integer.")
    async with _SYNC_LOCK:
        campaigns = await load_queued_campaigns(settings,pool=pool)
        if not campaigns:
            return {"campaigns_scanned": 0, "campaigns_available": 0, "marked_sent": 0}

        start = campaign_offset % len(campaigns)
        ordered_campaigns = campaigns[start:] + campaigns[:start]
        selected = ordered_campaigns[:MAX_CAMPAIGNS_PER_CYCLE]
        marked = 0
        provider_sends_seen = 0
        unmatched_sends = 0
        seen_provider_ids: set[str] = set()
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
                # Instantly distinguishes sequence sends (ue_type=1) from
                # manually sent messages (ue_type=3). Manual replies must never
                # consume a queued campaign step.
                if item.get("ue_type") not in (1, "1"):
                    continue
                # When the API identifies a message as a campaign email, count it
                # for diagnostics even when local reconciliation cannot match it.
                provider_sends_seen += 1
                provider_id = item.get("id")
                if not isinstance(provider_id, str) or not provider_id.strip() or provider_id in seen_provider_ids:
                    continue
                seen_provider_ids.add(provider_id)
                address = _provider_recipient(item)
                provider_lead_id = str(item.get("lead_id") or "")
                candidates = queued_by_provider_lead.get(provider_lead_id) or queued_by_recipient.get(address, [])
                if not candidates:
                    unmatched_sends += 1
                    continue
                subject = _normalized_subject(item.get("subject"))
                match = next((row for row in candidates if _normalized_subject(row.get("subject")) == subject), None)
                if not match:
                    unmatched_sends += 1
                    continue
                if await mark_email_sent(settings, str(match["id"]), provider_id, provider_sent_timestamp(item),pool=pool):
                    marked += 1
                    # These indexes reference the same row objects. Remove the
                    # matched step from both so duplicate provider records cannot
                    # reuse it through the recipient fallback index.
                    for indexed_rows in (queued_by_provider_lead.get(provider_lead_id), queued_by_recipient.get(address)):
                        if indexed_rows and match in indexed_rows:
                            indexed_rows.remove(match)
                else:
                    unmatched_sends += 1

        return {"campaigns_scanned": len(selected), "campaigns_available": len(campaigns), "marked_sent": marked,
                "provider_sends_seen": provider_sends_seen, "unmatched_sends": unmatched_sends}
