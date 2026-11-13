"""PostgreSQL human review queue and audit trail."""
from fastapi import HTTPException


async def list_pending(pool,limit,offset):
    async with pool.acquire() as conn:
        total=await conn.fetchval("select count(*) from public.personalized_emails where status='pending_review'")
        rows=await conn.fetch("""select pe.id,pe.lead_id,pe.campaign_id,pe.step_id,pe.subject,pe.body,pe.status,
            pe.risk_level,pe.risk_reason,pe.created_at,
            l.business_name,l.first_name,l.email,l.business_category,l.city,c.name campaign_name,c.instructions,
            s.step_number,s.context,s.trigger_type from public.personalized_emails pe
            join public.leads l on l.id=pe.lead_id join public.campaigns c on c.id=pe.campaign_id
            join public.campaign_steps s on s.id=pe.step_id where pe.status='pending_review'
            order by pe.created_at,pe.id limit $1 offset $2""",limit,offset)
    items=[]
    for row in rows:
        d=dict(row); d["id"]=str(d["id"]);d["lead_id"]=str(d["lead_id"]);d["campaign_id"]=str(d["campaign_id"]);d["step_id"]=str(d["step_id"])
        d["lead"]={k:d.pop(k) for k in ("business_name","first_name","email","business_category","city")}
        d["campaign"]={"name":d.pop("campaign_name"),"instructions":d.pop("instructions")}
        d["step"]={"step_number":d.pop("step_number"),"context":d.pop("context"),"trigger_type":d.pop("trigger_type")}
        items.append(d)
    return {"items":items,"total":int(total),"limit":limit,"offset":offset,"has_more":offset+len(items)<total}


async def list_history(pool,limit,offset,search,days):
    safe="%"+search.strip()+"%" if search.strip() else "%"
    where="($1='%' or business_name ilike $1 or recipient_email ilike $1 or campaign_name ilike $1 or subject ilike $1)"
    async with pool.acquire() as conn:
        if days:
            args=(safe,days,limit,offset)
            total=await conn.fetchval(f"select count(*) from public.email_review_history where {where} and created_at>=now()-make_interval(days=>$2)",*args[:2])
            rows=await conn.fetch(f"select id,email_id,action,status,risk_level,risk_reason,subject,business_name,recipient_email,campaign_name,step_number,created_at from public.email_review_history where {where} and created_at>=now()-make_interval(days=>$2) order by created_at desc,id desc limit $3 offset $4",*args)
        else:
            total=await conn.fetchval(f"select count(*) from public.email_review_history where {where}",safe)
            rows=await conn.fetch(f"select id,email_id,action,status,risk_level,risk_reason,subject,business_name,recipient_email,campaign_name,step_number,created_at from public.email_review_history where {where} order by created_at desc,id desc limit $2 offset $3",safe,limit,offset)
    return {"items":[dict(r)|{"id":str(r["id"]),"email_id":str(r["email_id"])} for r in rows],"total":int(total),"limit":limit,"offset":offset,"has_more":offset+len(rows)<total}


async def clear_history(pool):
    async with pool.acquire() as conn: await conn.execute("delete from public.email_review_history")
    return {"cleared":True}


async def review_context(pool,email_id):
    async with pool.acquire() as conn:
        row=await conn.fetchrow("""select pe.id,pe.lead_id,pe.campaign_id,pe.step_id,pe.subject,pe.body,pe.status,pe.risk_level,pe.risk_reason,
            l.business_name,l.first_name,l.email,l.business_category,l.city,l.phone,l.website,l.full_address,
            d.id draft_id,d.subject draft_subject,d.body draft_body,d.status draft_status,
            c.name campaign_name,c.instructions,s.step_number,s.context,s.trigger_type
            from public.personalized_emails pe join public.leads l on l.id=pe.lead_id
            join public.generic_drafts d on d.step_id=pe.step_id join public.campaigns c on c.id=pe.campaign_id
            join public.campaign_steps s on s.id=pe.step_id where pe.id=$1::uuid""",email_id)
    if not row:return None
    d=dict(row)
    return {"email":{k:d[k] for k in ("id","lead_id","campaign_id","step_id","subject","body","status","risk_level","risk_reason")}|{"id":str(d["id"]),"lead_id":str(d["lead_id"]),"campaign_id":str(d["campaign_id"]),"step_id":str(d["step_id"])},
        "lead":{"id":str(d["lead_id"]),**{k:d[k] for k in ("business_name","first_name","email","business_category","city","phone","website","full_address")}},
        "draft":{"id":str(d["draft_id"]),"step_id":str(d["step_id"]),"subject":d["draft_subject"],"body":d["draft_body"],"status":d["draft_status"]},
        "campaign":{"id":str(d["campaign_id"]),"name":d["campaign_name"],"instructions":d["instructions"]},
        "step":{"id":str(d["step_id"]),"step_number":d["step_number"],"context":d["context"],"trigger_type":d["trigger_type"]}}


async def update_email(pool,email_id,expected_status,values):
    async with pool.acquire() as conn:
        async with conn.transaction():
            context=await conn.fetchrow("""select pe.*,l.business_name,l.email recipient_email,c.name campaign_name,s.step_number
                from public.personalized_emails pe join public.leads l on l.id=pe.lead_id
                join public.campaigns c on c.id=pe.campaign_id join public.campaign_steps s on s.id=pe.step_id
                where pe.id=$1::uuid and pe.status=$2 for update of pe""",email_id,expected_status)
            if not context:raise HTTPException(status_code=409,detail="This email changed while you were reviewing it. Reload the queue.")
            allowed={"subject","body","risk_level","risk_reason","status","approved_version_hash","reviewed_at"}
            if set(values)-allowed:raise ValueError("Unsupported review update field.")
            names=list(values); args=[values[n] for n in names]
            assignments=",".join(f"{name}=${i+3}" for i,name in enumerate(names))
            row=await conn.fetchrow(f"update public.personalized_emails set {assignments} where id=$1::uuid and status=$2 returning *",email_id,expected_status,*args)
            if not row:raise HTTPException(status_code=409,detail="This email changed while you were reviewing it. Reload the queue.")
            new_status=values.get("status",expected_status)
            action={"queued":"approved","approved":"approved","rejected":"rejected","pending_review":"edited"}.get(new_status,"updated")
            await conn.execute("""insert into public.email_review_history(email_id,action,status,risk_level,risk_reason,subject,
                business_name,recipient_email,campaign_name,step_number) values($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                email_id,action,new_status,values.get("risk_level",context["risk_level"]),values.get("risk_reason",context["risk_reason"]),
                values.get("subject",context["subject"]),context["business_name"],context["recipient_email"],context["campaign_name"],context["step_number"])
    return dict(row)
