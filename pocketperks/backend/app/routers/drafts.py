from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.drafts_db import approve_draft, update_draft
from app.services.draft_service import generate_campaign_drafts, regenerate_draft

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


@router.post("/api/drafts/{draft_id}/regenerate")
async def regenerate(draft_id: str, settings: Settings = Depends(get_settings)):
    result = await regenerate_draft(settings, draft_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return result


@router.post("/api/drafts/{draft_id}/approve")
async def approve(draft_id: str, settings: Settings = Depends(get_settings)):
    return await approve_draft(settings, draft_id)
