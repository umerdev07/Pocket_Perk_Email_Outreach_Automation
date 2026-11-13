"""PostgreSQL repository for personalization and run progress."""
from fastapi import HTTPException


async def load_run_context(pool, run_id: str) -> dict:
    async with pool.acquire() as conn:
        run = await conn.fetchrow(
            "select id,campaign_id,batch_id,total_leads from public.campaign_runs where id=$1::uuid",
            run_id,
        )
        if not run:
            raise HTTPException(status_code=404, detail="Campaign run not found.")
        campaign = await conn.fetchrow(
            "select id,name,instructions,status from public.campaigns where id=$1::uuid", run["campaign_id"]
        )
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found for this run.")
        rows = await conn.fetch(
            """select s.id,s.step_number,s.context,s.trigger_type,d.id as draft_id,
                      d.subject,d.body,d.status as draft_status
                 from public.campaign_steps s
                 left join public.generic_drafts d on d.step_id=s.id
                where s.campaign_id=$1::uuid order by s.step_number""",
            run["campaign_id"],
        )
    steps = []
    for row in rows:
        if not row["draft_id"] or row["draft_status"] != "approved":
            raise HTTPException(status_code=409, detail="Every campaign step must have an approved draft.")
        steps.append({"id": str(row["id"]), "step_number": row["step_number"], "context": row["context"],
                      "trigger_type": row["trigger_type"],
                      "draft": {"id": str(row["draft_id"]), "subject": row["subject"],
                                "body": row["body"], "status": row["draft_status"]}})
    if not steps:
        raise HTTPException(status_code=409, detail="Campaign has no approved email steps.")
    return {"run": dict(run), "campaign": {**dict(campaign), "steps": steps}}


async def load_lead_page(pool, batch_id: str, *, offset: int, limit: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select id,business_name,first_name,business_category,email,city,website,
                      verification_result,quality_flags
                 from public.leads where batch_id=$1::uuid order by id limit $2 offset $3""",
            batch_id, limit, offset,
        )
    return [dict(row) for row in rows]


async def get_run_personalized_emails(pool, run_id: str, *, limit: int, offset: int) -> dict:
    async with pool.acquire() as conn:
        exists = await conn.fetchval("select exists(select 1 from public.campaign_runs where id=$1::uuid)", run_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Campaign run not found.")
        total = await conn.fetchval("select count(*) from public.personalized_emails where run_id=$1::uuid", run_id)
        rows = await conn.fetch(
            """select pe.id,pe.lead_id,pe.step_id,pe.subject,pe.body,pe.status,pe.manual_send_state,
                      pe.risk_level,pe.risk_reason,pe.created_at,
                      l.business_name,l.first_name,l.email,s.step_number,s.context,s.trigger_type
                 from public.personalized_emails pe
                 left join public.leads l on l.id=pe.lead_id
                 left join public.campaign_steps s on s.id=pe.step_id
                where pe.run_id=$1::uuid order by pe.created_at desc,pe.id desc limit $2 offset $3""",
            run_id, limit, offset,
        )
    items=[]
    for row in rows:
        item=dict(row)
        item["id"], item["lead_id"], item["step_id"] = map(str, (item["id"],item["lead_id"],item["step_id"]))
        item["lead"]={"business_name":item.pop("business_name"),"first_name":item.pop("first_name"),"email":item.pop("email")}
        item["step"]={"step_number":item.pop("step_number"),"context":item.pop("context"),"trigger_type":item.pop("trigger_type")}
        items.append(item)
    return {"items":items,"total":int(total),"limit":limit,"offset":offset,"has_more":offset+len(items)<total}


async def get_run_lead_personalized_emails(pool, run_id: str, lead_id: str) -> dict:
    async with pool.acquire() as conn:
        lead = await conn.fetchrow(
            "select id,business_name,first_name,email,business_category,city,website from public.leads where id=$1::uuid",
            lead_id,
        )
        rows = await conn.fetch(
            """select pe.id,pe.lead_id,pe.step_id,pe.subject,pe.body,pe.status,pe.manual_send_state,
                      pe.risk_level,pe.risk_reason,pe.created_at,s.step_number,s.context,s.trigger_type
                 from public.personalized_emails pe join public.campaign_steps s on s.id=pe.step_id
                where pe.run_id=$1::uuid and pe.lead_id=$2::uuid order by s.step_number limit 10""",
            run_id, lead_id,
        )
    if not rows:
        raise HTTPException(status_code=404, detail="No personalized emails found for this lead in this run.")
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")
    items=[]
    for row in rows:
        item=dict(row)
        for key in ("id","lead_id","step_id"):
            item[key]=str(item[key])
        item["step"]={"step_number":item.pop("step_number"),"context":item.pop("context"),"trigger_type":item.pop("trigger_type")}
        items.append(item)
    return {"lead":dict(lead),"items":items}


async def save_personalized_email(pool, email: dict) -> dict:
    async with pool.acquire() as conn:
        row=await conn.fetchrow(
            """insert into public.personalized_emails(run_id,lead_id,campaign_id,step_id,subject,body,
                      risk_level,risk_reason,status,send_mode)
                 values($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,$6,$7,$8,$9,$10)
                 on conflict(run_id,lead_id,step_id) do nothing returning *""",
            email["run_id"],email["lead_id"],email["campaign_id"],email["step_id"],email["subject"],email["body"],
            email.get("risk_level"),email.get("risk_reason"),email.get("status","queued"),email.get("send_mode","test"),
        )
    return dict(row) if row else {}


async def get_existing_personalization_keys(pool, run_id: str, lead_ids: list[str]) -> set[tuple[str,str]]:
    if not lead_ids:
        return set()
    async with pool.acquire() as conn:
        rows=await conn.fetch(
            "select lead_id,step_id from public.personalized_emails where run_id=$1::uuid and lead_id=any($2::uuid[])",
            run_id,lead_ids,
        )
    return {(str(row["lead_id"]),str(row["step_id"])) for row in rows}


async def update_run_progress(pool, run_id: str, *, processed: int, skipped: int, failed: int,
                              skip_log: list[dict] | None, status: str, finished: bool=False,
                              error_detail: str | None=None, failure_log: list[dict] | None=None) -> None:
    if status not in {"running","completed","failed"}:
        raise ValueError("Invalid campaign run status.")
    async with pool.acquire() as conn:
        result=await conn.execute(
            """update public.campaign_runs set processed=$2,skipped=$3,failed=$4,status=$5,
                      skip_log=coalesce($6::jsonb,skip_log),error_detail=$7,
                      failure_log=coalesce($8::jsonb,failure_log),
                      finished_at=case when $9 then now() else finished_at end
                where id=$1::uuid""",
            run_id,processed,skipped,failed,status,
            skip_log,
            (error_detail or "")[:1000] if error_detail is not None else None,
            failure_log[:500] if failure_log is not None else None,finished,
        )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404,detail="Campaign run not found.")


async def get_suppressed_emails(pool, emails: set[str] | list[str]) -> set[str]:
    normalized={str(email).strip().lower() for email in emails if str(email).strip()}
    if not normalized:
        return set()
    async with pool.acquire() as conn:
        rows=await conn.fetch("select email from public.email_suppressions where active and email=any($1::text[])",list(normalized))
    return {row["email"] for row in rows}
