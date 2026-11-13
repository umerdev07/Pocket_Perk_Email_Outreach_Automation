"""Native PostgreSQL draft persistence; background generation remains a separate port."""
from datetime import UTC, datetime


def _json_value(value):
    import json
    return json.loads(value) if isinstance(value, str) else value


async def load_campaign(pool, campaign_id: str) -> dict | None:
    row = await pool.fetchrow("select id,name,instructions,status from public.campaigns where id=$1::uuid", campaign_id)
    if row is None:
        return None
    campaign = dict(row)
    steps = await pool.fetch("""select s.id,s.campaign_id,s.step_number,s.context,s.trigger_type,
        to_jsonb(d) as generic_drafts from public.campaign_steps s
        left join public.generic_drafts d on d.step_id=s.id
        where s.campaign_id=$1::uuid order by s.step_number""", campaign_id)
    campaign["campaign_steps"] = []
    for step in steps:
        item = dict(step)
        draft = _json_value(item["generic_drafts"])
        item["generic_drafts"] = draft if draft else None
        campaign["campaign_steps"].append(item)
    return campaign


async def get_draft_context(pool, draft_id: str) -> dict | None:
    row = await pool.fetchrow("""select d.*,s.id as step_id,s.campaign_id,s.step_number,s.context,s.trigger_type,
        c.id as parent_campaign_id,c.name as campaign_name,c.instructions,c.status as campaign_status
        from public.generic_drafts d join public.campaign_steps s on s.id=d.step_id
        join public.campaigns c on c.id=s.campaign_id where d.id=$1::uuid""", draft_id)
    if row is None:
        return None
    result = dict(row)
    result["campaign_steps"] = {"id": str(result.pop("step_id")), "campaign_id": str(result.pop("campaign_id")),
        "step_number": result.pop("step_number"), "context": result.pop("context"),
        "trigger_type": result.pop("trigger_type"),
        "campaigns": {"id": str(result.pop("parent_campaign_id")), "name": result.pop("campaign_name"),
                      "instructions": result.pop("instructions"), "status": result.pop("campaign_status")}}
    return result


async def prepare_step_draft(pool, step_id: str) -> dict:
    now = datetime.now(UTC)
    row = await pool.fetchrow("""insert into public.generic_drafts(step_id,subject,body,status,generation_status,
        generation_started_at,generation_finished_at,generation_error)
        values($1::uuid,'','','generated','generating',$2,null,null)
        on conflict(step_id) do update set generation_status='generating',generation_error=null,
        generation_started_at=$2,generation_finished_at=null,updated_at=$2 returning *""", step_id, now)
    return dict(row)


async def upsert_step_draft(pool, step_id: str, subject: str, body: str) -> dict:
    row = await pool.fetchrow("""insert into public.generic_drafts(step_id,subject,body,status,approved_at,
        generation_status,generation_error,generation_finished_at)
        values($1::uuid,$2,$3,'generated',null,'succeeded',null,$4)
        on conflict(step_id) do update set subject=$2,body=$3,status='generated',approved_at=null,
        generation_status='succeeded',generation_error=null,generation_finished_at=$4 returning *""",
        step_id, subject, body, datetime.now(UTC))
    return dict(row)


async def set_generation_state(pool, draft_id: str, state: str, error: str | None = None) -> dict:
    now = datetime.now(UTC)
    row = await pool.fetchrow("""update public.generic_drafts set generation_status=$2,generation_error=$3,
        generation_started_at=case when $2='generating' then $4 else generation_started_at end,
        generation_finished_at=case when $2 in ('succeeded','failed') then $4 else null end,updated_at=$4
        where id=$1::uuid returning *""", draft_id, state, error, now)
    if row is None:
        raise LookupError("Draft not found.")
    return dict(row)


async def approve_draft(pool, draft_id: str) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""update public.generic_drafts set status='approved',approved_at=$2,updated_at=$2
                where id=$1::uuid returning *""", draft_id, datetime.now(UTC))
            if row is None:
                raise LookupError("Draft not found.")
            campaign_id = await conn.fetchval("select campaign_id from public.campaign_steps where id=$1", row["step_id"])
            remaining = await conn.fetchval("""select count(*) from public.campaign_steps s left join public.generic_drafts d
                on d.step_id=s.id where s.campaign_id=$1 and d.status is distinct from 'approved'""", campaign_id)
            if remaining == 0:
                await conn.execute("update public.campaigns set status='approved' where id=$1", campaign_id)
            return dict(row)


async def update_draft(pool, draft_id: str, subject: str, body: str) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""update public.generic_drafts d set subject=$2,body=$3,status='edited',
                approved_at=null,updated_at=now() from public.campaign_steps s
                where d.id=$1::uuid and s.id=d.step_id returning d.*""", draft_id, subject, body)
            if row is None:
                raise LookupError("Draft not found.")
            await conn.execute("""update public.campaigns set status='draft' where id=(select campaign_id
                from public.campaign_steps where id=$1) and status='approved'""", row["step_id"])
            return dict(row)
