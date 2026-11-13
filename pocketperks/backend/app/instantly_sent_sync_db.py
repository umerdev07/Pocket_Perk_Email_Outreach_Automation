"""Read and reconcile PostgreSQL email rows with confirmed Instantly sends."""

from datetime import UTC, datetime

from fastapi import HTTPException

from app.config import Settings


def _pool_or_fail(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable for sent-email sync.")
    return pool


async def load_queued_campaigns(settings: Settings, pool=None) -> list[dict]:
    pool = _pool_or_fail(pool)
    async with pool.acquire() as conn:
        mappings = await conn.fetch("""select id,run_id,instantly_campaign_id,status
            from public.instantly_campaign_mappings where status=any($1::text[])
            and instantly_campaign_id is not null order by created_at limit 1000""", ["leads_synced", "active"])
        result = []
        for mapping in mappings:
            emails = await conn.fetch("""select pe.id,pe.run_id,pe.campaign_id,pe.lead_id,pe.step_id,pe.subject,pe.created_at,
                l.email,s.step_number,lm.instantly_lead_id from public.personalized_emails pe
                join public.leads l on l.id=pe.lead_id join public.campaign_steps s on s.id=pe.step_id
                left join public.instantly_lead_mappings lm on lm.campaign_mapping_id=$1 and lm.lead_id=pe.lead_id and lm.status='synced'
                where pe.run_id=$2 and pe.status='queued' order by pe.created_at limit 1000""", mapping["id"], mapping["run_id"])
            if emails:
                result.append({**dict(mapping), "id": str(mapping["id"]), "run_id": str(mapping["run_id"]),
                    "queued_emails": [{**dict(e), "id": str(e["id"]), "run_id": str(e["run_id"]),
                        "campaign_id": str(e["campaign_id"]), "lead_id": str(e["lead_id"]),
                        "step_id": str(e["step_id"]), "leads": {"email": e["email"]},
                        "campaign_steps": {"step_number": e["step_number"]}} for e in emails]})
    return result


async def mark_email_sent(settings: Settings, email_id: str, provider_email_id: str,
                          sent_at: str, pool=None) -> bool:
    async with _pool_or_fail(pool).acquire() as conn:
        row = await conn.fetchrow("""update public.personalized_emails set status='sent',sent_at=$2::timestamptz,
            instantly_ref=$3,send_error=null where id=$1::uuid and status='queued' returning id""",
            email_id, sent_at, provider_email_id)
    return row is not None


def provider_sent_timestamp(item: dict) -> str:
    value = item.get("timestamp_email") or item.get("timestamp_created")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed).astimezone(UTC).isoformat()
        except ValueError:
            pass
    return datetime.now(UTC).isoformat()
