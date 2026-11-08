"""Supabase persistence for campaign endpoints; kept separate for easy test fakes."""
import httpx
from fastapi import HTTPException

from app.config import Settings


def _headers(settings: Settings) -> dict[str, str]:
    key = settings.supabase_service_key
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for campaigns.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


async def _save_step_triggers(settings: Settings, steps: list[dict], triggers: list[str]) -> None:
    """Persist manual conversation triggers against the IDs created by the schedule RPC."""
    updates = [{"step_id": step["id"], "trigger_type": trigger}
               for index, (step, trigger) in enumerate(zip(steps, triggers, strict=True)) if index > 0]
    if not updates:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{_base(settings)}/rpc/set_campaign_step_triggers",
            headers=_headers(settings), json={"step_triggers": updates},
        )
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Campaign was saved, but email trigger settings could not be saved. Apply migration 033 and retry editing the campaign.")


async def create_campaign(settings: Settings, campaign: dict, contexts: list[str], delays_minutes: list[int],
                          triggers: list[str]) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{_base(settings)}/rpc/create_campaign_with_schedule",
            headers=_headers(settings),
            json={"campaign_data": campaign, "step_contexts": contexts,
                  "step_delays_minutes": delays_minutes},
        )
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not save the campaign.")
    payload = response.json()
    result = payload[0] if isinstance(payload, list) else payload
    await _save_step_triggers(settings, result.get("steps", []), triggers)
    for step, trigger in zip(result.get("steps", []), triggers, strict=True):
        step["trigger_type"] = trigger
    return result


async def list_campaigns(settings: Settings) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_base(settings)}/campaigns", params={
            "select": "id,name,total_emails,start_date,end_date,start_at,end_at,timezone,daily_send_start,daily_send_end,sending_days,status,created_at,campaign_steps(step_number,delay_days,delay_minutes,trigger_type),campaign_runs(id)", "order": "created_at.desc",
        }, headers=_headers(settings))
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load campaigns.")
    campaigns = response.json()
    for campaign in campaigns:
        campaign["has_runs"] = bool(campaign.pop("campaign_runs", []))
        campaign["steps"] = sorted(campaign.pop("campaign_steps", []),
                                   key=lambda step: step.get("step_number", 0))
    return campaigns


async def update_campaign(settings: Settings, campaign_id: str, campaign: dict,
                          contexts: list[str], delays_minutes: list[int], triggers: list[str]) -> dict:
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(
            f"{_base(settings)}/rpc/update_campaign_with_schedule",
            headers=_headers(settings),
            json={"target_campaign_id": campaign_id, "campaign_data": campaign,
                  "step_contexts": contexts, "step_delays_minutes": delays_minutes},
        )
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not save campaign changes.")
    result = response.json()
    if isinstance(result, list):
        result = result[0] if result else {}
    reason = result.get("reason")
    if reason == "not_found":
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if reason == "campaign_started":
        raise HTTPException(status_code=409, detail="This campaign already has a run and can no longer be edited safely.")
    if reason == "draft_generation_running":
        raise HTTPException(status_code=409, detail="Campaign drafts are currently being generated. Wait for generation to finish before editing.")
    if result.get("updated") is not True:
        raise HTTPException(status_code=503, detail="Campaign update could not be confirmed.")
    await _save_step_triggers(settings, result.get("steps", []), triggers)
    for step, trigger in zip(result.get("steps", []), triggers, strict=True):
        step["trigger_type"] = trigger
    return result


async def get_campaign(settings: Settings, campaign_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_base(settings)}/campaigns", params={
            "select": "id,name,total_emails,start_date,end_date,start_at,end_at,timezone,daily_send_start,daily_send_end,sending_days,instructions,status,created_at,campaign_steps(id,step_number,context,delay_days,delay_minutes,trigger_type,generic_drafts(id,subject,body,status,approved_at,generation_status,generation_error,generation_started_at,generation_finished_at))",
            "id": f"eq.{campaign_id}", "limit": "1",
        }, headers=_headers(settings))
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load the campaign.")
    rows = response.json()
    if not rows:
        return None
    campaign = rows[0]
    for step in campaign.get("campaign_steps", []):
        relation = step.pop("generic_drafts", None)
        # PostgREST returns a one-to-one relation as an object and a one-to-many
        # relation as a list. generic_drafts.step_id is unique, so handle both.
        if isinstance(relation, list):
            draft = relation[0] if relation else None
        elif isinstance(relation, dict):
            draft = relation
        else:
            draft = None
        step["draft"] = draft
    campaign["steps"] = sorted(campaign.pop("campaign_steps", []), key=lambda item: item["step_number"])
    return campaign


async def delete_campaign(settings: Settings, campaign_id: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{_base(settings)}/rpc/delete_campaign_and_related_data",
                                     headers=_headers(settings), json={"target_campaign_id": campaign_id})
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not delete the campaign.")
    result = response.json()
    if isinstance(result, list):
        result = result[0] if result else {}
    if result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if result.get("reason") == "active_run":
        raise HTTPException(status_code=409, detail="This campaign has a run in progress. Wait for it to finish before deleting.")
    if result.get("deleted") is not True:
        raise HTTPException(status_code=503, detail="Campaign deletion could not be confirmed.")


async def delete_campaign_step(settings: Settings, campaign_id: str, step_id: str) -> dict:
    """Remove one draft step atomically; the SQL function also renumbers remaining steps."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{_base(settings)}/rpc/delete_campaign_step",
                                     headers=_headers(settings), json={
                                         "target_campaign_id": campaign_id,
                                         "target_step_id": step_id,
                                     })
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not delete this campaign email step.")
    result = response.json()
    if isinstance(result, list):
        result = result[0] if result else {}
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
