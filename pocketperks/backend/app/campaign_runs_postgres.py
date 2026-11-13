"""PostgreSQL implementation of the campaign run API."""
from fastapi import HTTPException

from app.campaign_runs_db import _campaign_window_estimate


async def start_campaign_run(pool, campaign_id: str, batch_id: str, sender_emails: list[str] | None,
                            sender_accounts: list[dict] | None) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(hashtext($1),hashtext($2))", campaign_id,batch_id)
            campaign = await conn.fetchrow(
                """select c.id,c.status,c.start_at,c.end_at,c.timezone,c.daily_send_start,
                          c.daily_send_end,c.sending_days,
                          coalesce(jsonb_agg(jsonb_build_object('step_number',s.step_number,
                            'delay_days',s.delay_days,'delay_minutes',s.delay_minutes,
                            'trigger_type',s.trigger_type) order by s.step_number)
                            filter(where s.id is not null),'[]'::jsonb) as campaign_steps
                     from public.campaigns c left join public.campaign_steps s on s.campaign_id=c.id
                    where c.id=$1::uuid group by c.id""", campaign_id
            )
            if not campaign:
                raise HTTPException(status_code=404, detail="Campaign not found.")
            if campaign["status"] != "approved":
                raise HTTPException(status_code=409, detail="Campaign must be approved before it can be started.")
            steps = campaign["campaign_steps"]
            for index, step in enumerate(steps):
                if index and step.get("trigger_type", "scheduled") == "scheduled":
                    delay = step.get("delay_minutes")
                    if delay is None:
                        delay = int(step.get("delay_days") or 0) * 1440
                    if delay != 4320:
                        raise HTTPException(status_code=422, detail="This campaign uses an older follow-up interval. Update and approve it again; automatic follow-ups now use a fixed 3-day wait.")
            batch = await conn.fetchrow("select id,imported_count from public.lead_batches where id=$1::uuid for share", batch_id)
            if not batch:
                raise HTTPException(status_code=404, detail="Lead batch not found.")
            existing = await conn.fetchval(
                "select exists(select 1 from public.campaign_runs where campaign_id=$1::uuid and batch_id=$2::uuid)",
                campaign_id,batch_id)
            if existing:
                raise HTTPException(status_code=409, detail="This campaign has already been started with this lead batch. Use its existing run, or import a new batch to start another run.")
            estimate = _campaign_window_estimate(dict(campaign), int(batch["imported_count"]), sender_accounts) if sender_accounts else None
            if estimate and estimate.get("too_long"):
                raise HTTPException(status_code=422, detail="This batch would need more than two years at the selected inbox limits. Split the batch or select additional active inboxes before starting.")
            end_at = estimate.get("recommended_end_at") if estimate else None
            row = await conn.fetchrow(
                """insert into public.campaign_runs(campaign_id,batch_id,status,total_leads,
                          sender_account_emails,schedule_end_at)
                     values($1::uuid,$2::uuid,'running',$3,$4,$5::timestamptz) returning id,status,total_leads""",
                campaign_id,batch_id,int(batch["imported_count"]),sender_emails or [],end_at)
    output={"run_id":str(row["id"]),"status":row["status"],"total_leads":row["total_leads"]}
    if end_at:
        output["schedule_adjustment"]={"previous_end_at":campaign["end_at"].isoformat(),
            "new_end_at":end_at,"timezone":campaign["timezone"],"buffer_sending_windows":1}
    return output


async def get_campaign_run(pool, run_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row=await conn.fetchrow(
            """select id,campaign_id,batch_id,status,total_leads,processed,skipped,failed,error_detail,
                      failure_log,created_at,finished_at,schedule_end_at
                 from public.campaign_runs where id=$1::uuid""",run_id)
    if not row: return None
    data=dict(row)
    return {"run_id":str(data["id"]),"campaign_id":str(data["campaign_id"]),"batch_id":str(data["batch_id"]),
        "status":data["status"],"total_leads":data["total_leads"],"processed":data["processed"],
        "skipped":data["skipped"],"failed":data["failed"],"created_at":data["created_at"],
        "finished_at":data["finished_at"],"error_detail":data["error_detail"],
        "failure_log":data["failure_log"] or [],"schedule_end_at":data["schedule_end_at"]}


async def get_campaign_run_instantly_status(pool, run_id: str) -> dict:
    async with pool.acquire() as conn:
        run=await conn.fetchrow("select total_leads from public.campaign_runs where id=$1::uuid",run_id)
        if not run: raise HTTPException(status_code=404,detail="Campaign run not found.")
        mapping=await conn.fetchrow("select id,instantly_campaign_id,status,error_detail,sender_account_emails,updated_at from public.instantly_campaign_mappings where run_id=$1::uuid",run_id)
        if not mapping:
            return {"run_id":run_id,"status":"waiting","instantly_campaign_id":None,"synced_leads":0,
                "pending_leads":0,"failed_leads":0,"skipped_leads":0,"error_detail":None,"sender_account_emails":[]}
        counts=await conn.fetchrow("""select count(*) filter(where status='synced') as synced,
            count(*) filter(where status='pending') as pending,count(*) filter(where status='failed') as failed,
            count(*) filter(where status='skipped') as skipped,count(*) filter(where status='unknown') as unknown
            from public.instantly_lead_mappings where campaign_mapping_id=$1::uuid""",mapping["id"])
    status=mapping["status"]
    error=mapping["error_detail"]
    if status=="failed" and error=="No eligible leads synced to Instantly." and counts["pending"]:
        status,error="awaiting_review","Waiting for personalized email review before lead sync."
    if status=="syncing_leads" and counts["synced"]+counts["pending"]+counts["failed"]+counts["skipped"]+counts["unknown"] >= run["total_leads"] and not (counts["pending"] or counts["failed"] or counts["unknown"]):
        status,error="leads_synced",None
    return {"run_id":run_id,"status":status,"instantly_campaign_id":mapping["instantly_campaign_id"],
        "synced_leads":counts["synced"],"pending_leads":counts["pending"],"failed_leads":counts["failed"],
        "skipped_leads":counts["skipped"],"unknown_leads":counts["unknown"],"error_detail":error,
        "sender_account_emails":mapping["sender_account_emails"] or [],"updated_at":mapping["updated_at"]}
