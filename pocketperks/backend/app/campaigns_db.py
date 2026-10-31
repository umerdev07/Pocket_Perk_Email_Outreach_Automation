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


async def create_campaign(settings: Settings, campaign: dict, contexts: list[str]) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{_base(settings)}/rpc/create_campaign_with_steps",
            headers=_headers(settings),
            json={"campaign_data": campaign, "step_contexts": contexts},
        )
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not save the campaign.")
    payload = response.json()
    return payload[0] if isinstance(payload, list) else payload


async def list_campaigns(settings: Settings) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_base(settings)}/campaigns", params={
            "select": "id,name,total_emails,start_date,end_date,status,created_at", "order": "created_at.desc",
        }, headers=_headers(settings))
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load campaigns.")
    return response.json()


async def get_campaign(settings: Settings, campaign_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_base(settings)}/campaigns", params={
            "select": "id,name,total_emails,start_date,end_date,instructions,status,created_at,campaign_steps(id,step_number,context,delay_days,generic_drafts(id,subject,body,status,approved_at))",
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
