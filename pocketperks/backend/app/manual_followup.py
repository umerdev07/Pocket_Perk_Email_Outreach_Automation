"""Explicit, thread-bound sending for visit/conversation follow-up emails."""

from datetime import UTC, datetime

from fastapi import HTTPException

from app.config import Settings
from app.integrations.instantly_client import (AuthenticationError, InstantlyError, InstantlyWriteError,
    MissingAPIKeyError, PlanAccessError, RateLimitedError, get_email, reply_to_email)
from app.services.email_footer import append_outreach_footer
from app.suppression_db import get_suppressed_emails


async def send_after_conversation(settings: Settings, email_id: str, confirmed: bool,
                                  *, expected_run_id: str, pool=None) -> dict:
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    if settings.send_mode != "live":
        raise HTTPException(status_code=409, detail="Manual Instantly sending requires SEND_MODE=live.")
    if confirmed is not True:
        raise HTTPException(status_code=422, detail="Confirm that a visit or conversation took place before sending this email.")

    async with pool.acquire() as db:
        item = await db.fetchrow("""select pe.id,pe.run_id,pe.lead_id,pe.step_id,pe.subject,pe.body,pe.status,
            pe.manual_send_state,l.email recipient_email,s.trigger_type from public.personalized_emails pe
            join public.leads l on l.id=pe.lead_id join public.campaign_steps s on s.id=pe.step_id
            where pe.id=$1::uuid and pe.run_id=$2::uuid""", email_id, expected_run_id)
        if not item:
            raise HTTPException(status_code=404, detail="Personalized email not found.")
        if item["trigger_type"] != "after_conversation":
            raise HTTPException(status_code=409, detail="This email is not configured for a confirmed conversation or visit.")
        if item["status"] != "approved":
            raise HTTPException(status_code=409, detail="This email must be reviewed and ready before manual sending.")
        if item["manual_send_state"]:
            raise HTTPException(status_code=409, detail="This email was already sent or has an unresolved send outcome; reconcile it before retrying.")
        recipient = str(item["recipient_email"] or "").strip().lower()
        if not recipient:
            raise HTTPException(status_code=409, detail="The lead has no valid recipient email address.")
        if recipient in await get_suppressed_emails(settings, {recipient}, pool=pool):
            raise HTTPException(status_code=409, detail="This recipient is suppressed and cannot receive another email.")
        earlier = await db.fetch("""select pe.id,pe.step_id,pe.instantly_ref,pe.sent_at,s.step_number
            from public.personalized_emails pe join public.campaign_steps s on s.id=pe.step_id
            where pe.run_id=$1::uuid and pe.lead_id=$2::uuid and pe.status='sent'
            and pe.instantly_ref is not null and pe.step_id<>$3::uuid order by s.step_number desc limit 20""",
            str(item["run_id"]), str(item["lead_id"]), str(item["step_id"]))
    if not earlier:
        raise HTTPException(status_code=409, detail="No earlier confirmed Instantly email exists to reply to; the message was not sent.")
    try:
        provider_thread = await get_email(str(earlier[0]["instantly_ref"]), settings=settings)
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=f"Could not load the existing Instantly thread: {exc}") from exc
    sender = str(provider_thread.get("eaccount") or "").strip().lower()
    if "@" not in sender:
        raise HTTPException(status_code=409, detail="Instantly did not return the original sending mailbox for this thread.")
    async with pool.acquire() as db:
        claimed = await db.fetchrow("""update public.personalized_emails set manual_send_state='sending'
            where id=$1::uuid and status='approved' and manual_send_state is null returning id""", email_id)
    if not claimed:
        raise HTTPException(status_code=409, detail="This email changed while sending was being prepared. Reload before retrying.")
    body = append_outreach_footer(str(item["body"] or ""), settings)
    try:
        sent = await reply_to_email(str(earlier[0]["instantly_ref"]), sender,
                                    str(item["subject"] or ""), body, settings=settings)
    except Exception as exc:
        ambiguous = not isinstance(exc, (AuthenticationError, MissingAPIKeyError, PlanAccessError, RateLimitedError)) and not (
            isinstance(exc, InstantlyWriteError) and not exc.outcome_unknown)
        async with pool.acquire() as db:
            await db.execute("update public.personalized_emails set manual_send_state=$2,send_error=$3 where id=$1::uuid",
                email_id, "unknown" if ambiguous else None,
                "Instantly reply outcome needs reconciliation before retry." if ambiguous else str(exc)[:300])
        detail = str(exc) if isinstance(exc, InstantlyError) else "Instantly reply outcome is unknown."
        suffix = " This email is held from retry to prevent duplicates." if ambiguous else " Correct the provider issue and retry."
        raise HTTPException(status_code=502, detail=f"{detail}{suffix}") from exc
    now = datetime.now(UTC)
    async with pool.acquire() as db:
        saved = await db.fetchrow("""update public.personalized_emails set manual_send_state='sent',status='sent',
            sent_at=$2,instantly_ref=$3,send_error=null where id=$1::uuid and manual_send_state='sending' returning id""",
            email_id, now, sent.get("id"))
    if not saved:
        raise HTTPException(status_code=503, detail="Instantly confirmed the reply, but PocketPerks could not save its sent status. Refresh and reconcile before retrying.")
    return {"email_id": email_id, "sent": True, "sent_at": now.isoformat(),
            "sender_email": sender, "provider_email_id": sent.get("id")}
