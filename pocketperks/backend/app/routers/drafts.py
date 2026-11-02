from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.drafts_db import approve_draft, update_draft, get_draft_context, set_generation_state, demote_campaign
from app.services.draft_service import generate_campaign_drafts, run_regeneration_job

router = APIRouter(tags=["drafts"])


class DraftEdit(BaseModel):
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


@router.post("/api/campaigns/{campaign_id}/drafts/generate")
async def generate(campaign_id: str, settings: Settings = Depends(get_settings)):
    result = await generate_campaign_drafts(settings, campaign_id)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return result


@router.patch("/api/drafts/{draft_id}")
async def edit(draft_id: str, payload: DraftEdit, settings: Settings = Depends(get_settings)):
    return await update_draft(settings, draft_id, payload.subject.strip(), payload.body.strip())


@router.post("/api/drafts/{draft_id}/regenerate", status_code=202)
async def regenerate(draft_id: str, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings)):
    draft = await get_draft_context(settings, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.get("generation_status") == "generating":
        return {"id": draft_id, "generation_status": "generating"}
    step = draft.get("campaign_steps") or {}
    campaign = step.get("campaigns") or {}
    if campaign.get("status") == "approved":
        await demote_campaign(settings, campaign.get("id") or step.get("campaign_id"))
    await set_generation_state(settings, draft_id, "generating")
    background_tasks.add_task(run_regeneration_job, settings, draft_id)
    return {"id": draft_id, "generation_status": "generating"}


@router.post("/api/drafts/{draft_id}/approve")
async def approve(draft_id: str, settings: Settings = Depends(get_settings)):
    return await approve_draft(settings, draft_id)
