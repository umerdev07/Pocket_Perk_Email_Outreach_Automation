"""Native PostgreSQL campaign repository; routes switch only after draft parity."""

import json


async def create_campaign(pool, campaign: dict, contexts: list[str], delays: list[int], triggers: list[str]) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.fetchval(
                "select public.create_campaign_with_schedule($1::jsonb,$2::jsonb,$3::jsonb)",
                campaign, contexts, delays)
            result = json.loads(result) if isinstance(result, str) else result
            trigger_updates = [{"step_id": step["id"], "trigger_type": trigger}
                               for i, (step, trigger) in enumerate(zip(result["steps"], triggers, strict=True)) if i]
            if trigger_updates:
                await conn.fetchval("select public.set_campaign_step_triggers($1::jsonb)", trigger_updates)
            for step, trigger in zip(result["steps"], triggers, strict=True):
                step["trigger_type"] = trigger
            return result


async def list_campaigns(pool) -> list[dict]:
    rows = await pool.fetch("""select c.*, exists(select 1 from public.campaign_runs r where r.campaign_id=c.id) has_runs
        from public.campaigns c order by c.created_at desc""")
    result = []
    for row in rows:
        campaign = dict(row)
        steps = await pool.fetch("""select id,step_number,context,delay_days,delay_minutes,trigger_type
            from public.campaign_steps where campaign_id=$1 order by step_number""", campaign["id"])
        campaign["steps"] = [dict(step) for step in steps]
        campaign["id"] = str(campaign["id"])
        result.append(campaign)
    return result


async def get_campaign(pool, campaign_id: str) -> dict | None:
    row = await pool.fetchrow("select * from public.campaigns where id=$1::uuid", campaign_id)
    if row is None:
        return None
    campaign = dict(row)
    steps = await pool.fetch("""select s.*, to_jsonb(d) draft from public.campaign_steps s
        left join public.generic_drafts d on d.step_id=s.id where s.campaign_id=$1 order by s.step_number""", row["id"])
    campaign["steps"] = []
    for step in steps:
        item = dict(step)
        item["draft"] = json.loads(item["draft"]) if isinstance(item.get("draft"), str) else item.get("draft")
        campaign["steps"].append(item)
    campaign["id"] = str(campaign["id"])
    return campaign


async def update_campaign(pool, campaign_id: str, campaign: dict, contexts: list[str], delays: list[int], triggers: list[str]) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.fetchval("select public.update_campaign_with_schedule($1::uuid,$2::jsonb,$3::jsonb,$4::jsonb)",
                campaign_id, campaign, contexts, delays)
            result = json.loads(result) if isinstance(result, str) else result
            reason = result.get("reason")
            if reason:
                from fastapi import HTTPException
                status, detail = {
                    "not_found": (404, "Campaign not found."),
                    "campaign_started": (409, "This campaign already has a run and can no longer be edited safely."),
                    "draft_generation_running": (409, "Campaign drafts are currently being generated. Wait for generation to finish before editing."),
                }.get(reason, (503, "Campaign changes could not be confirmed."))
                raise HTTPException(status_code=status, detail=detail)
            if result.get("updated"):
                updates = [{"step_id": s["id"], "trigger_type": t} for i,(s,t) in enumerate(zip(result["steps"],triggers,strict=True)) if i]
                if updates:
                    await conn.fetchval("select public.set_campaign_step_triggers($1::jsonb)", updates)
                for step, trigger in zip(result["steps"], triggers, strict=True):
                    step["trigger_type"] = trigger
            return result


async def delete_campaign(pool, campaign_id: str) -> None:
    result = await pool.fetchval("select public.delete_campaign_and_related_data($1::uuid)", campaign_id)
    result = json.loads(result) if isinstance(result, str) else result
    if result.get("reason") == "not_found":
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if result.get("reason") == "active_run":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="This campaign has a run in progress. Wait for it to finish before deleting.")
    if result.get("deleted") is not True:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Campaign deletion could not be confirmed.")


async def delete_campaign_step(pool, campaign_id: str, step_id: str) -> dict:
    result = await pool.fetchval("select public.delete_campaign_step($1::uuid,$2::uuid)", campaign_id, step_id)
    result = json.loads(result) if isinstance(result, str) else result
    from fastapi import HTTPException
    reason = result.get("reason")
    if reason == "not_found":
        raise HTTPException(status_code=404, detail="Campaign or email step not found.")
    if reason == "last_step":
        raise HTTPException(status_code=409, detail="A campaign must keep at least one email step.")
    if reason == "campaign_started":
        raise HTTPException(status_code=409, detail="This campaign already has a run; its email steps cannot be changed.")
    if result.get("deleted") is not True:
        raise HTTPException(status_code=503, detail="Email step deletion could not be confirmed.")
    return result
