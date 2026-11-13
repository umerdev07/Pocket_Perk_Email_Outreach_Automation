"""Persist Instantly bounce events and conservatively suppress unsafe addresses."""

from datetime import UTC, datetime
import re

from fastapi import HTTPException

from app.config import Settings
from app.suppression_db import normalize_email


def _text(value: object, limit: int = 500) -> str | None:
    if isinstance(value, dict):
        value = value.get("message") or value.get("reason") or value.get("response") or value.get("code")
    if not isinstance(value, (str, int, float)):
        return None
    return " ".join(str(value).split())[:limit] or None


def _bounce_type(payload: dict) -> str:
    raw = " ".join(_text(payload.get(k), 200) or "" for k in
        ("bounce_type", "bounceType", "classification", "bounce_status", "smtp_code", "status_code", "code")).casefold()
    reason = " ".join(_text(payload.get(k), 500) or "" for k in
        ("bounce_reason", "reason", "error", "error_message", "smtp_response", "response")).casefold()
    combined = f"{raw} {reason}"
    if any(x in combined for x in ("hard", "permanent", "invalid recipient", "user unknown", "mailbox not found")):
        return "hard"
    if any(x in combined for x in ("soft", "temporary", "transient", "mailbox full", "try again")):
        return "soft"
    if re.search(r"\b5[.\s-]?\d{1,2}[.\s-]?\d{1,2}\b|\b5\d{2}\b", combined):
        return "hard"
    if re.search(r"\b4[.\s-]?\d{1,2}[.\s-]?\d{1,2}\b|\b4\d{2}\b", combined):
        return "soft"
    return "unknown"


def _event_time(payload: dict) -> str:
    for key in ("timestamp", "event_timestamp", "created_at", "timestamp_created"):
        value = payload.get(key)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return (parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed).astimezone(UTC).isoformat()
            except ValueError:
                pass
    return datetime.now(UTC).isoformat()


def _step_number(payload: dict) -> int | None:
    value = payload.get("step") or payload.get("step_number")
    if isinstance(value, dict):
        value = value.get("number") or value.get("step_number")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 10 else None


async def save_bounce_event(settings: Settings, event_key: str, payload: dict, pool=None) -> None:
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable for bounce tracking.")
    nested = payload.get("data") or payload.get("payload")
    if isinstance(nested, dict):
        payload = {**payload, **nested}
    raw_email = payload.get("lead_email") or payload.get("email") or payload.get("recipient_email")
    if isinstance(raw_email, dict):
        raw_email = raw_email.get("email")
    email = normalize_email(raw_email)
    provider_campaign_id = _text(payload.get("campaign_id") or payload.get("campaignId"), 200)
    bounce_type = _bounce_type(payload)
    reason = None
    for key in ("bounce_reason", "reason", "error_message", "smtp_response", "response", "error"):
        reason = _text(payload.get(key))
        if reason:
            break
    if not reason and bounce_type == "unknown":
        reason = "Bounce type could not be determined from the provider event."
    email_account = _text(payload.get("email_account") or payload.get("emailAccount"), 320)
    event_at, step_number = _event_time(payload), _step_number(payload)
    async with pool.acquire() as conn:
        async with conn.transaction():
            mapping = await conn.fetchrow("select id,run_id from public.instantly_campaign_mappings where instantly_campaign_id=$1", provider_campaign_id) if provider_campaign_id else None
            run_id = str(mapping["run_id"]) if mapping else None
            campaign_id = await conn.fetchval("select campaign_id from public.campaign_runs where id=$1::uuid", run_id) if run_id else None
            lead_id = await conn.fetchval("select id from public.leads where email=$1", email) if email else None
            matched = bool(mapping and lead_id and await conn.fetchval("""select exists(select 1 from public.instantly_lead_mappings
                where campaign_mapping_id=$1::uuid and lead_id=$2::uuid and status='synced')""", mapping["id"], lead_id))
            inserted = await conn.fetchval("""insert into public.instantly_bounce_events(event_key,provider_campaign_id,campaign_mapping_id,
                run_id,campaign_id,lead_id,email,bounce_type,reason,step_number,email_account,event_at,match_status)
                values($1,$2,$3::uuid,$4::uuid,$5::uuid,$6::uuid,$7,$8,$9,$10,$11,$12::timestamptz,$13)
                on conflict(event_key) do nothing returning event_key""", event_key, provider_campaign_id,
                str(mapping["id"]) if mapping else None, run_id, str(campaign_id) if campaign_id else None,
                str(lead_id) if lead_id else None, email, bounce_type, reason, step_number, email_account,
                event_at, "matched" if matched else "unmatched")
            if not inserted:
                return
            if matched and run_id and lead_id:
                if step_number is None:
                    await conn.execute("""update public.personalized_emails set status='cancelled',
                        send_error='Stopped by the sending provider after a bounce.'
                        where run_id=$1::uuid and lead_id=$2::uuid and status='queued'""", run_id, lead_id)
                else:
                    await conn.execute("""update public.personalized_emails pe set status='bounced',bounced_at=$3::timestamptz,
                        bounce_type=$4,send_error=$5 from public.campaign_steps s where pe.step_id=s.id and pe.run_id=$1::uuid
                        and pe.lead_id=$2::uuid and s.step_number=$6 and pe.status in ('queued','sent')""", run_id, lead_id,
                        event_at, bounce_type, reason or "The recipient mail server rejected this email.", step_number)
                    await conn.execute("""update public.personalized_emails pe set status='cancelled',
                        send_error='Stopped by the sending provider after an earlier bounce.' from public.campaign_steps s
                        where pe.step_id=s.id and pe.run_id=$1::uuid and pe.lead_id=$2::uuid and s.step_number>$3 and pe.status='queued'""",
                        run_id, lead_id, step_number)
            if email and bounce_type in {"hard", "unknown"}:
                description = "Hard bounce" if bounce_type == "hard" else "Unclassified bounce; verify this address before future emails."
                if reason:
                    description += f": {reason}"
                await conn.fetchval("select public.record_email_suppressions($1::jsonb)", [{"email": email,
                    "source": "bounce_auto", "reason": description, "lead_id": str(lead_id) if lead_id else None,
                    "campaign_id": str(campaign_id) if campaign_id else None}])


async def get_bounce_summary(settings: Settings, pool=None) -> dict:
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable for bounce activity.")
    async with pool.acquire() as conn:
        counts = await conn.fetchrow("""select count(*) total,count(*) filter(where bounce_type='hard') hard,
            count(*) filter(where bounce_type='soft') soft,count(*) filter(where bounce_type='unknown') unknown
            from public.instantly_bounce_events""")
        recent = await conn.fetch("""select b.event_key,b.email,b.bounce_type,b.reason,b.step_number,b.email_account,b.event_at,
            b.match_status,l.business_name,c.name campaign_name from public.instantly_bounce_events b
            left join public.leads l on l.id=b.lead_id left join public.campaigns c on c.id=b.campaign_id
            order by b.event_at desc limit 10""")
    return {"available": True, **{key: int(counts[key] or 0) for key in ("total", "hard", "soft", "unknown")},
            "recent": [dict(row) for row in recent]}
