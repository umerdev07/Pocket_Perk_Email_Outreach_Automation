"""Process queued Instantly events without making webhook delivery synchronous."""
import asyncio
import logging

from app.config import Settings
from app.bounce_db import save_bounce_event
from app.instantly_replies_service import sync_recent_replies
from app.instantly_sent_sync_service import sync_sent_email_statuses
from app.replies_db import save_received_replies
from app.suppression_db import normalize_email, save_suppressions
from app.webhook_db import claim_instantly_webhooks, finish_instantly_webhook

logger = logging.getLogger(__name__)


async def _process_event(settings: Settings, payload: dict, event_key: str | None = None, pool=None) -> None:
    event_type = str(payload.get("event_type") or payload.get("type") or "").strip().casefold()
    event_type = event_type.replace("-", "_").replace(" ", "_")
    if event_type in {"reply_received", "auto_reply_received", "email_reply_received"}:
        # The provider inbox read is authoritative; this also picks up full
        # message bodies and lets existing STOP detection run before cursor advance.
        await sync_recent_replies(settings, max_pages=5,pool=pool)
        return
    if event_type == "email_sent":
        await sync_sent_email_statuses(settings,pool=pool)
        return
    if event_type == "email_bounced":
        if not event_key:
            raise ValueError("Bounce event is missing its deduplication key.")
        await save_bounce_event(settings, event_key, payload,pool=pool)
        return
    if event_type in {"email_opened", "link_clicked", "account_error",
                      "campaign_completed", "lead_neutral", "lead_interested",
                      "lead_not_interested", "lead_meeting_booked", "lead_meeting_completed",
                      "lead_closed", "lead_out_of_office", "lead_wrong_person"}:
        # These events are retained as processed delivery signals. The present
        # dashboard has no corresponding provider metric/state column yet.
        return
    if event_type in {"lead_unsubscribed", "email_unsubscribed", "lead_opted_out"}:
        address = normalize_email(payload.get("lead_email") or payload.get("email") or payload.get("lead"))
        if address:
            await save_suppressions(settings, [{
                "email": address, "source": "reply_auto",
                "reason": "Recipient unsubscribe event received from Instantly.",
            }],pool=pool)


async def run_instantly_webhook_worker(settings: Settings, pool=None) -> None:
    while True:
        try:
            events = await claim_instantly_webhooks(settings, limit=10,pool=pool)
            for event in events:
                event_id = event.get("id")
                payload = event.get("payload")
                if not isinstance(event_id, int) or not isinstance(payload, dict):
                    continue
                try:
                    event_key = event.get("event_key")
                    await _process_event(settings, payload, event_key if isinstance(event_key, str) else None,pool=pool)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Instantly webhook event will retry after %s.", type(exc).__name__)
                    await finish_instantly_webhook(settings, event_id,
                        error=f"Processing retry: {type(exc).__name__}", attempts=int(event.get("attempts") or 1),pool=pool)
                else:
                    await finish_instantly_webhook(settings, event_id,pool=pool)
            await asyncio.sleep(5 if events else 30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Instantly webhook queue will retry after %s.", type(exc).__name__)
            await asyncio.sleep(30)
