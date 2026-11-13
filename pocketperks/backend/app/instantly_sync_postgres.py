"""PostgreSQL repository for local Instantly campaign/lead mappings."""
from datetime import datetime, UTC
from fastapi import HTTPException


async def create_campaign_mapping(pool,run_id,sender_emails=None):
    async with pool.acquire() as conn:
        row=await conn.fetchrow("""insert into public.instantly_campaign_mappings(run_id,sender_account_emails)
            values($1::uuid,$2) on conflict(run_id) do update set run_id=excluded.run_id returning *""",run_id,sender_emails or [])
    return dict(row)


async def update_campaign_mapping(pool,mapping_id,values):
    allowed={"instantly_campaign_id","status","error_detail","sender_account_emails"}
    if not values or set(values)-allowed:raise ValueError("Only provider campaign ID, status, and safe error detail can be updated.")
    async with pool.acquire() as conn:
        row=await conn.fetchrow("""update public.instantly_campaign_mappings set
            instantly_campaign_id=coalesce($2,instantly_campaign_id),status=coalesce($3,status),
            error_detail=case when $4 then $5 else error_detail end,
            sender_account_emails=coalesce($6,sender_account_emails),updated_at=now()
            where id=$1::uuid returning *""",mapping_id,values.get("instantly_campaign_id"),values.get("status"),
            "error_detail" in values,values.get("error_detail"),values.get("sender_account_emails"))
    if not row:raise HTTPException(status_code=404,detail="Instantly campaign mapping not found.")
    return dict(row)


async def get_campaign_mapping_for_run(pool,run_id):
    async with pool.acquire() as conn:
        row=await conn.fetchrow("select id,run_id,instantly_campaign_id,status,error_detail,sender_account_emails from public.instantly_campaign_mappings where run_id=$1::uuid",run_id)
    return dict(row) if row else None


async def load_active_sender_usage(pool):
    async with pool.acquire() as conn:
        rows=await conn.fetch("select run_id,status,sender_account_emails from public.instantly_campaign_mappings where status=any($1::text[]) and instantly_campaign_id is not null",["active","scheduled","launching"])
    by={}
    for r in rows:
        for sender in r["sender_account_emails"] or []:by.setdefault(sender.strip().lower(),[]).append({"run_id":str(r["run_id"]),"status":r["status"]})
    return {"active_campaign_count":len(rows),"by_sender":by}


async def load_scheduled_campaign_windows(pool):
    async with pool.acquire() as conn:
        rows=await conn.fetch("""select m.id,m.run_id,m.instantly_campaign_id,m.sender_account_emails,
            c.start_at,coalesce(r.schedule_end_at,c.end_at) end_at from public.instantly_campaign_mappings m
            join public.campaign_runs r on r.id=m.run_id join public.campaigns c on c.id=r.campaign_id
            where m.status='scheduled' order by m.updated_at limit 100""")
    return [dict(r)|{"id":str(r["id"]),"run_id":str(r["run_id"])} for r in rows]


async def save_lead_mappings(pool,mapping_id,rows):
    if not rows:return 0
    allowed={"pending","synced","skipped","failed","unknown"}
    for row in rows:
        if not isinstance(row,dict) or not row.get("lead_id") or row.get("status") not in allowed:
            raise ValueError("Each lead mapping needs a lead ID and valid sync status.")
        if row["status"]=="synced" and not row.get("instantly_lead_id"):
            raise ValueError("A synced lead mapping requires an Instantly lead ID.")
    async with pool.acquire() as conn:
        async with conn.transaction():
            for row in rows:
                if not row.get("lead_id") or row.get("status") not in allowed:raise ValueError("Each lead mapping needs a lead ID and valid sync status.")
                if row.get("status")=="synced" and not row.get("instantly_lead_id"):raise ValueError("A synced lead mapping requires an Instantly lead ID.")
                await conn.execute("""insert into public.instantly_lead_mappings(campaign_mapping_id,lead_id,instantly_lead_id,status,error_detail,synced_at,updated_at)
                    values($1::uuid,$2::uuid,$3,$4,$5,$6::timestamptz,now()) on conflict(campaign_mapping_id,lead_id)
                    do update set instantly_lead_id=excluded.instantly_lead_id,status=excluded.status,error_detail=excluded.error_detail,
                    synced_at=coalesce(excluded.synced_at,instantly_lead_mappings.synced_at),updated_at=now()""",
                    mapping_id,row["lead_id"],row.get("instantly_lead_id"),row["status"],row.get("error_detail"),row.get("synced_at"))
    return len(rows)


async def get_lead_mapping_statuses(pool,mapping_id,lead_ids):
    if not lead_ids:return {}
    async with pool.acquire() as conn:
        rows=await conn.fetch("select lead_id,instantly_lead_id,status,error_detail from public.instantly_lead_mappings where campaign_mapping_id=$1::uuid and lead_id=any($2::uuid[])",mapping_id,lead_ids)
    return {str(r["lead_id"]):dict(r)|{"lead_id":str(r["lead_id"])} for r in rows}


async def load_sync_run_context(pool,run_id):
    async with pool.acquire() as conn:
        run=await conn.fetchrow("select id,campaign_id,batch_id,status,sender_account_emails,schedule_end_at from public.campaign_runs where id=$1::uuid",run_id)
        if not run:raise HTTPException(status_code=404,detail="Campaign run not found.")
        campaign=await conn.fetchrow("""select id,name,total_emails,start_date,end_date,start_at,end_at,timezone,daily_send_start,
            daily_send_end,sending_days,status from public.campaigns where id=$1::uuid""",run["campaign_id"])
        steps=await conn.fetch("""select s.id,s.step_number,s.delay_days,s.delay_minutes,s.trigger_type,d.id draft_id,d.status draft_status
            from public.campaign_steps s left join public.generic_drafts d on d.step_id=s.id where s.campaign_id=$1::uuid order by s.step_number""",run["campaign_id"])
    if run["status"]!="completed":raise HTTPException(status_code=409,detail="Personalization must finish before Instantly sync.")
    if not run["sender_account_emails"]:raise HTTPException(status_code=409,detail="Select at least one Instantly sending inbox before starting this campaign.")
    if not campaign:raise HTTPException(status_code=404,detail="Campaign not found.")
    c=dict(campaign); r=dict(run)
    if c["status"]!="approved":raise HTTPException(status_code=409,detail="Campaign must remain approved before Instantly sync.")
    if r["schedule_end_at"]:
        c["end_at"]=r["schedule_end_at"]
        c["end_date"]=r["schedule_end_at"].astimezone(__import__('zoneinfo').ZoneInfo(c["timezone"])).date().isoformat()
    normalized=[]
    for step in steps:
        if not step["draft_id"] or step["draft_status"]!="approved":raise HTTPException(status_code=409,detail="Every campaign step needs an approved draft before Instantly sync.")
        normalized.append({"id":str(step["id"]),"step_number":step["step_number"],"delay_days":step["delay_days"],"delay_minutes":step["delay_minutes"],"trigger_type":step["trigger_type"],"draft":{"id":str(step["draft_id"]),"status":step["draft_status"]}})
    if len(normalized)!=c["total_emails"] or (normalized and normalized[0]["trigger_type"]!="scheduled"):
        raise HTTPException(status_code=409,detail="Every campaign step needs an approved draft before Instantly sync.")
    c["steps"]=normalized
    return {"run":r,"campaign":c}


async def load_sync_lead_page(pool,run_id,batch_id,*,offset,limit):
    async with pool.acquire() as conn:
        leads=await conn.fetch("""select id,business_name,first_name,email,phone,website,business_category,city,verification_result,quality_flags
            from public.leads where batch_id=$1::uuid order by id limit $2 offset $3""",batch_id,limit,offset)
        result=[]
        for lead in leads:
            emails=await conn.fetch("""select pe.id,pe.lead_id,pe.step_id,pe.subject,pe.body,pe.status,pe.risk_level,s.step_number,s.trigger_type
                from public.personalized_emails pe join public.campaign_steps s on s.id=pe.step_id
                where pe.run_id=$1::uuid and pe.lead_id=$2::uuid order by s.step_number""",run_id,lead["id"])
            item=dict(lead);item["id"]=str(item["id"]);item["personalized_steps"]=[dict(e)|{"id":str(e["id"]),"lead_id":str(e["lead_id"]),"step_id":str(e["step_id"])} for e in emails]
            result.append(item)
    return result


async def load_active_campaign_windows(pool,*,limit=100):
    async with pool.acquire() as conn:
        rows=await conn.fetch("""select m.id,m.run_id,m.instantly_campaign_id,m.status,coalesce(r.schedule_end_at,c.end_at) end_at
            from public.instantly_campaign_mappings m join public.campaign_runs r on r.id=m.run_id
            join public.campaigns c on c.id=r.campaign_id where m.status=any($1::text[]) and m.instantly_campaign_id is not null
            order by m.updated_at limit $2""",["active","launching"],limit)
    return [dict(r)|{"id":str(r["id"]),"run_id":str(r["run_id"])} for r in rows]


async def load_active_runs_with_pending_leads(pool,*,limit=100,auto_launch=False):
    states=["active","awaiting_review","syncing_leads","failed"]
    if auto_launch:states.append("leads_synced")
    async with pool.acquire() as conn:
        rows=await conn.fetch("""select distinct m.run_id from public.instantly_campaign_mappings m
            where m.status=any($1::text[]) and m.instantly_campaign_id is not null and
            (m.status in ('syncing_leads','active','awaiting_review','leads_synced') or m.error_detail='No eligible leads synced to Instantly.')
            order by m.run_id limit $2""",states,limit)
        # Only recovery rows with pending/unknown leads; the scheduler state is
        # included when live auto-launch is enabled.
        found=[]
        for row in rows:
            state=await conn.fetchval("select status from public.instantly_campaign_mappings where run_id=$1",row["run_id"])
            if state=="leads_synced" and not auto_launch:continue
            pending=await conn.fetchval("select exists(select 1 from public.instantly_lead_mappings lm join public.instantly_campaign_mappings m on m.id=lm.campaign_mapping_id where m.run_id=$1 and lm.status=any(array['pending','unknown']))",row["run_id"])
            if pending or state=="leads_synced":found.append(str(row["run_id"]))
    return found
