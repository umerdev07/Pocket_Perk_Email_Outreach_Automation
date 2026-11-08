"""Explicit, thread-bound sending for visit/conversation follow-up emails."""
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.integrations.instantly_client import (AuthenticationError, InstantlyError,
                                               InstantlyWriteError, MissingAPIKeyError,
                                               PlanAccessError, RateLimitedError,
                                               get_email, reply_to_email)
from app.personalization_db import _base, _headers, _get
from app.services.email_footer import append_outreach_footer
from app.suppression_db import get_suppressed_emails


async def send_after_conversation(settings: Settings, email_id: str, confirmed: bool,
                                  *, expected_run_id: str) -> dict:
    if settings.send_mode != "live":
        raise HTTPException(status_code=409, detail="Manual Instantly sending requires SEND_MODE=live.")
    if confirmed is not True:
        raise HTTPException(status_code=422, detail="Confirm that a visit or conversation took place before sending this email.")

    base, headers = _base(settings), _headers(settings)
    async with httpx.AsyncClient(timeout=25) as db:
        rows = await _get(db, f"{base}/personalized_emails", {
            "select": "id,run_id,lead_id,campaign_id,step_id,subject,body,status,manual_send_state,campaign_steps(step_number,trigger_type),leads(email)",
            "id": f"eq.{email_id}", "run_id": f"eq.{expected_run_id}", "limit": "1",
        }, headers)
        if not rows:
            raise HTTPException(status_code=404, detail="Personalized email not found.")
        item = rows[0]
        step = item.get("campaign_steps") or {}
        lead = item.get("leads") or {}
        if isinstance(step, list):
            step = step[0] if step else {}
        if isinstance(lead, list):
            lead = lead[0] if lead else {}
        if step.get("trigger_type") != "after_conversation":
            raise HTTPException(status_code=409, detail="This email is not configured for a confirmed conversation or visit.")
        if item.get("status") != "approved":
            raise HTTPException(status_code=409, detail="This email must be reviewed and ready before manual sending.")
        if item.get("manual_send_state"):
            raise HTTPException(status_code=409, detail="This email was already sent or has an unresolved send outcome; reconcile it before retrying.")

        recipient = str(lead.get("email") or "").strip().lower()
        if not recipient:
            raise HTTPException(status_code=409, detail="The lead has no valid recipient email address.")
        if recipient in await get_suppressed_emails(settings, {recipient}):
            raise HTTPException(status_code=409, detail="This recipient is suppressed and cannot receive another email.")

        earlier = await _get(db, f"{base}/personalized_emails", {
            "select": "id,step_id,instantly_ref,sent_at,campaign_steps(step_number)",
            "run_id": f"eq.{item['run_id']}", "lead_id": f"eq.{item['lead_id']}",
            "status": "eq.sent", "instantly_ref": "not.is.null", "limit": "20",
        }, headers)
        earlier = [row for row in earlier if row.get("step_id") != item["step_id"]]
        def step_number(row: dict) -> int:
            relation = row.get("campaign_steps") or {}
            if isinstance(relation, list):
                relation = relation[0] if relation else {}
            return int(relation.get("step_number") or 0) if isinstance(relation, dict) else 0
        earlier.sort(key=step_number, reverse=True)
        if not earlier:
            raise HTTPException(status_code=409, detail="No earlier confirmed Instantly email exists to reply to; the message was not sent.")

    try:
        provider_thread = await get_email(str(earlier[0]["instantly_ref"]), settings=settings)
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=f"Could not load the existing Instantly thread: {exc}") from exc
    sender = str(provider_thread.get("eaccount") or "").strip().lower()
    if "@" not in sender:
        raise HTTPException(status_code=409, detail="Instantly did not return the original sending mailbox for this thread.")

    # Compare-and-set claim prevents a double click from sending twice. If the
    # provider call has an ambiguous result, keep 'unknown' and require review.
    async with httpx.AsyncClient(timeout=20) as db:
        claimed = await db.patch(f"{base}/personalized_emails", params={
            "id": f"eq.{email_id}", "status": "eq.approved", "manual_send_state": "is.null",
        }, headers={**headers, "Prefer": "return=representation"}, json={
            "manual_send_state": "sending",
        })
    if not claimed.is_success or not claimed.json():
        raise HTTPException(status_code=409, detail="This email changed while sending was being prepared. Reload before retrying.")

    body = append_outreach_footer(str(item.get("body") or ""), settings)
    try:
        sent = await reply_to_email(str(earlier[0]["instantly_ref"]), sender,
                                    str(item.get("subject") or ""), body, settings=settings)
    except Exception as exc:
        ambiguous = not isinstance(exc, (AuthenticationError, MissingAPIKeyError,
                                         PlanAccessError, RateLimitedError)) and not (
            isinstance(exc, InstantlyWriteError) and not exc.outcome_unknown)
        async with httpx.AsyncClient(timeout=20) as db:
            await db.patch(f"{base}/personalized_emails", params={"id": f"eq.{email_id}"},
                           headers=headers, json={"manual_send_state": "unknown" if ambiguous else None,
                                                  "send_error": "Instantly reply outcome needs reconciliation before retry." if ambiguous else str(exc)[:300]})
        detail = str(exc) if isinstance(exc, InstantlyError) else "Instantly reply outcome is unknown."
        suffix = " This email is held from retry to prevent duplicates." if ambiguous else " Correct the provider issue and retry."
        raise HTTPException(status_code=502, detail=f"{detail}{suffix}") from exc

    now = datetime.now(UTC).isoformat()
    async with httpx.AsyncClient(timeout=20) as db:
        saved = await db.patch(f"{base}/personalized_emails", params={
            "id": f"eq.{email_id}", "manual_send_state": "eq.sending",
        }, headers={**headers, "Prefer": "return=representation"}, json={
            "manual_send_state": "sent", "status": "sent", "sent_at": now,
            "instantly_ref": sent.get("id"), "send_error": None,
        })
    if not saved.is_success or not saved.json():
        raise HTTPException(status_code=503, detail="Instantly confirmed the reply, but PocketPerks could not save its sent status. Refresh and reconcile before retrying.")
    return {"email_id": email_id, "sent": True, "sent_at": now, "sender_email": sender,
            "provider_email_id": sent.get("id")}
