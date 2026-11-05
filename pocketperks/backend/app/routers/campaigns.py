from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo
from uuid import UUID

from app.campaigns_db import (create_campaign, delete_campaign, delete_campaign_step,
                              get_campaign, list_campaigns, update_campaign)
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1)
    total_emails: int = Field(ge=1, le=10)
    instructions: str = Field(min_length=1)
    start_at: datetime | None = None
    end_at: datetime | None = None
    # Backward-compatible date-only input for existing API clients.
    start_date: date | None = None
    end_date: date | None = None
    contexts: list[str]

    @model_validator(mode="after")
    def validate_contexts(self):
        if len(self.contexts) != self.total_emails:
            raise ValueError("contexts must contain one item per email step")
        if any(not context.strip() for context in self.contexts):
            raise ValueError("each context must contain non-empty text")
        london = ZoneInfo("Europe/London")
        if self.start_at is None and self.start_date is not None:
            self.start_at = datetime.combine(self.start_date, time.min)
        if self.end_at is None and self.end_date is not None:
            self.end_at = datetime.combine(self.end_date, time.max.replace(microsecond=0))
        if self.start_at is None or self.end_at is None:
            raise ValueError("Provide campaign start and end date and time.")
        if self.start_at.tzinfo is None:
            self.start_at = _resolve_london_local_time(self.start_at, london)
        if self.end_at.tzinfo is None:
            self.end_at = _resolve_london_local_time(self.end_at, london)
        if self.end_at <= self.start_at:
            raise ValueError("Campaign end time must be after its start time.")
        self.start_date = self.start_at.astimezone(london).date()
        self.end_date = self.end_at.astimezone(london).date()
        return self


class CampaignUpdate(CampaignCreate):
    """Full replacement of editable campaign setup before its first run."""


def _resolve_london_local_time(value: datetime, zone: ZoneInfo) -> datetime:
    """Interpret datetime-local form values as UK time and reject DST gaps/folds."""
    candidate = value.replace(tzinfo=zone)
    round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != value:
        raise ValueError("This local time does not exist in the UK because of a clock change.")
    other_fold = value.replace(tzinfo=zone, fold=1)
    if candidate.utcoffset() != other_fold.utcoffset():
        raise ValueError("This local time is ambiguous in the UK because of a clock change.")
    return candidate


@router.post("")
async def create_campaign_endpoint(payload: CampaignCreate, settings: Settings = Depends(get_settings)):
    campaign = payload.model_dump(mode="json", exclude={"contexts"}) | {"status": "draft"}
    result = await create_campaign(settings, campaign, payload.contexts)
    return result


@router.get("")
async def list_campaigns_endpoint(settings: Settings = Depends(get_settings)):
    return await list_campaigns(settings)


@router.patch("/{campaign_id}")
async def update_campaign_endpoint(campaign_id: UUID, payload: CampaignUpdate,
                                   settings: Settings = Depends(get_settings)):
    campaign = payload.model_dump(mode="json", exclude={"contexts"})
    return await update_campaign(settings, str(campaign_id), campaign, payload.contexts)


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
