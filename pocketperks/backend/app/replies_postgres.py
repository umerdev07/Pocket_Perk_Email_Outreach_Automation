"""PostgreSQL persistence for replies and the Instantly reply cursor."""
from datetime import UTC, datetime
from fastapi import HTTPException

from app.replies_db import _reply_mailbox_address, _explicit_opt_out
from app.suppression_db import normalize_email


async def list_replies(pool, settings, *, limit=100, offset=0):
    async with pool.acquire() as conn:
        rows=await conn.fetch("""select r.id,r.lead_id,r.campaign_id,r.from_email,r.sender_account_email,r.subject,r.body,
                r.instantly_id,r.received_at,r.status,r.created_at,l.business_name,l.email lead_email,c.name campaign_name
                from public.replies r left join public.leads l on l.id=r.lead_id
                left join public.campaigns c on c.id=r.campaign_id
                order by r.received_at desc limit $1 offset $2""",limit,offset)
    opted_out_records=[]
    for row in rows:
        address=normalize_email(row["lead_email"] or row["from_email"])
        if address and _explicit_opt_out(str(row["body"] or "")):
            opted_out_records.append({"email":address,"source":"reply_auto","reason":"Recipient explicitly requested no further email.",
                "lead_id":str(row["lead_id"]) if row["lead_id"] else None,
                "campaign_id":str(row["campaign_id"]) if row["campaign_id"] else None})
    if opted_out_records:
        from app.suppression_db import get_recorded_suppression_emails,save_suppressions
        recorded=await get_recorded_suppression_emails(None,{r["email"] for r in opted_out_records},pool=pool)
        await save_suppressions(None,[r for r in opted_out_records if r["email"] not in recorded],pool=pool)
    items=[]
    for row in rows:
        d=dict(row); address=(d.pop("lead_email") or d.get("from_email") or "").lower()
        business=d.pop("business_name"); campaign=d.pop("campaign_name")
        d["id"]=str(d["id"]); d["lead_id"]=str(d["lead_id"]) if d["lead_id"] else None
        d["campaign_id"]=str(d["campaign_id"]) if d["campaign_id"] else None
        d["leads"]={"business_name":business,"email":address} if business or address else {}
        d["campaigns"]={"name":campaign} if campaign else {}
        d["opted_out"]=address in await _get_suppressed(pool,{address}) if address else False
        items.append(d)
    return items


async def _get_suppressed(pool, emails):
    if not emails:return set()
    async with pool.acquire() as conn:
        rows=await conn.fetch("select email from public.email_suppressions where active and email=any($1::text[])",list(emails))
    return {r["email"] for r in rows}


async def save_received_replies(pool, emails):
    if not emails:return 0
    pending=[]; optouts=[]
    async with pool.acquire() as conn:
        for item in emails:
            provider_id=item.get("id"); sender=item.get("from_address_email")
            lead_email=str(item.get("lead") or sender or "").strip().lower()
            if not isinstance(provider_id,str) or not provider_id or not isinstance(sender,str) or not sender.strip():continue
            lead=await conn.fetchrow("select id,email from public.leads where email=$1",lead_email)
            campaign_id=await conn.fetchval("""select r.campaign_id from public.instantly_campaign_mappings m
                join public.campaign_runs r on r.id=m.run_id where m.instantly_campaign_id=$1""",str(item.get("campaign_id") or ""))
            body_obj=item.get("body") if isinstance(item.get("body"),dict) else {}
            body=body_obj.get("text") or item.get("content_preview") or "(Reply body unavailable in provider response.)"
            received=item.get("timestamp_email") or item.get("timestamp_created")
            try: received_at=datetime.fromisoformat(str(received).replace("Z","+00:00")) if received else datetime.now(UTC)
            except ValueError: received_at=datetime.now(UTC)
            inserted=await conn.fetchval("""insert into public.replies(lead_id,campaign_id,from_email,sender_account_email,
                subject,body,instantly_id,received_at,status) values($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9)
                on conflict(instantly_id) do nothing returning id""",
                lead["id"] if lead else None,campaign_id,sender.strip().lower(),_reply_mailbox_address(item),
                str(item.get("subject") or "(no subject)")[:500],str(body)[:20000],provider_id,received_at,
                "new" if item.get("is_unread") else "read")
            if inserted:pending.append(inserted)
            normalized=(lead["email"] if lead else lead_email).lower()
            if normalized and _explicit_opt_out(str(body)):
                optouts.append({"email":normalized,"source":"reply_auto","reason":"Recipient explicitly requested no further email.",
                    "lead_id":str(lead["id"]) if lead else None,"campaign_id":str(campaign_id) if campaign_id else None})
        # Must record suppression before the provider sync cursor is advanced.
        if optouts:
            from app.suppression_db import get_recorded_suppression_emails
            recorded=await get_recorded_suppression_emails(None,{r["email"] for r in optouts},pool=pool)
            optouts=[record for record in optouts if record["email"] not in recorded]
            from app.suppression_db import save_suppressions
            await save_suppressions(None,optouts,pool=pool)
    return len(pending)


async def mark_reply_read(pool, reply_id):
    async with pool.acquire() as conn:
        row=await conn.fetchrow("update public.replies set status='read' where id=$1::uuid returning *",reply_id)
    if not row:raise HTTPException(status_code=404,detail="Reply not found.")
    return dict(row)


async def reply_context(pool, reply_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("select id,instantly_id,sender_account_email,subject from public.replies where id=$1::uuid",reply_id)


async def set_reply_mailbox(pool,reply_id,mailbox):
    async with pool.acquire() as conn:
        await conn.execute("update public.replies set sender_account_email=$2 where id=$1::uuid and sender_account_email is null",reply_id,mailbox)


async def delete_reply(pool,reply_id):
    async with pool.acquire() as conn:
        await conn.execute("delete from public.replies where id=$1::uuid",reply_id)
    return {"reply_id":reply_id,"deleted":True}


async def opt_out_context(pool,reply_id):
    async with pool.acquire() as conn:
        row=await conn.fetchrow("""select r.id,r.lead_id,r.campaign_id,r.from_email,l.email from public.replies r
            left join public.leads l on l.id=r.lead_id where r.id=$1::uuid""",reply_id)
    return row


async def get_sync_state(pool):
    async with pool.acquire() as conn:
        row=await conn.fetchrow("select last_checked_at,sync_from,starting_after from public.instantly_reply_sync_state where id=true")
    if not row:raise HTTPException(status_code=503,detail="Instantly reply sync cursor is missing.")
    return dict(row)


async def update_sync_state(pool, *, last_checked_at=None,sync_from=None,starting_after=None):
    if isinstance(last_checked_at, str):
        last_checked_at = datetime.fromisoformat(last_checked_at.replace("Z", "+00:00"))
    if isinstance(sync_from, str):
        sync_from = datetime.fromisoformat(sync_from.replace("Z", "+00:00"))
    async with pool.acquire() as conn:
        await conn.execute("""update public.instantly_reply_sync_state set last_checked_at=coalesce($1,last_checked_at),
            sync_from=$2,starting_after=$3,updated_at=now() where id=true""",last_checked_at,sync_from,starting_after)
