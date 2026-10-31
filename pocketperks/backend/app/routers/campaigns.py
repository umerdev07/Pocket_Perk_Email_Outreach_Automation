from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from datetime import date
from uuid import UUID

from app.campaigns_db import create_campaign, delete_campaign, delete_campaign_step, get_campaign, list_campaigns
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1)
    total_emails: int = Field(ge=1, le=10)
    instructions: str = Field(min_length=1)
    start_date: date
    end_date: date
    contexts: list[str]

    @model_validator(mode="after")
    def validate_contexts(self):
        if len(self.contexts) != self.total_emails:
            raise ValueError("contexts must contain one item per email step")
        if any(not context.strip() for context in self.contexts):
            raise ValueError("each context must contain non-empty text")
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


@router.post("")
async def create_campaign_endpoint(payload: CampaignCreate, settings: Settings = Depends(get_settings)):
    campaign = payload.model_dump(mode="json", exclude={"contexts"}) | {"status": "draft"}
    result = await create_campaign(settings, campaign, payload.contexts)
    return result


@router.get("")
async def list_campaigns_endpoint(settings: Settings = Depends(get_settings)):
    return await list_campaigns(settings)


@router.get("/{campaign_id}")
async def get_campaign_endpoint(campaign_id: str, settings: Settings = Depends(get_settings)):
    result = await get_campaign(settings, campaign_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return result


@router.delete("/{campaign_id}/steps/{step_id}")
async def delete_campaign_step_endpoint(campaign_id: UUID, step_id: UUID,
                                        settings: Settings = Depends(get_settings)):
    return await delete_campaign_step(settings, str(campaign_id), str(step_id))


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign_endpoint(campaign_id: UUID, settings: Settings = Depends(get_settings)):
    await delete_campaign(settings, str(campaign_id))
