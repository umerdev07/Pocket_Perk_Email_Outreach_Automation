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
    timezone: str = "Europe/London"
    daily_send_start: time | None = None
    daily_send_end: time | None = None
    sending_days: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    # Backward-compatible date-only input for existing API clients.
    start_date: date | None = None
    end_date: date | None = None
    contexts: list[str]
    step_delays_minutes: list[int] | None = None
    step_triggers: list[str] | None = None

    @model_validator(mode="after")
    def validate_contexts(self):
        if len(self.contexts) != self.total_emails:
            raise ValueError("contexts must contain one item per email step")
        if any(not context.strip() for context in self.contexts):
            raise ValueError("each context must contain non-empty text")
        if self.step_delays_minutes is None:
            self.step_delays_minutes = [0, *([4320] * (self.total_emails - 1))]
        if len(self.step_delays_minutes) != self.total_emails:
            raise ValueError("step_delays_minutes must contain one delay per email step")
        if self.step_delays_minutes[0] != 0 or any(type(delay) is not int or delay < (0 if i == 0 else 15) or delay > 43200
                                                   for i, delay in enumerate(self.step_delays_minutes)):
            raise ValueError("Email 1 must have no delay; follow-up delays must be 15 minutes to 30 days")
        if self.step_triggers is None:
            self.step_triggers = ["scheduled"] * self.total_emails
        if (len(self.step_triggers) != self.total_emails or self.step_triggers[0] != "scheduled"
                or (len(self.step_triggers) > 1 and self.step_triggers[1] != "scheduled")
                or any(trigger not in {"scheduled", "after_conversation", "close_loop"} for trigger in self.step_triggers)):
            raise ValueError("Emails 1 and 2 must be scheduled; later steps can be timed, close-loop, or after_conversation.")
        if not self.sending_days or len(set(self.sending_days)) != len(self.sending_days) or any(day < 0 or day > 6 for day in self.sending_days):
            raise ValueError("Select at least one unique sending day (0 Sunday through 6 Saturday)")
        try:
            campaign_zone = ZoneInfo(self.timezone)
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("Provide a valid IANA timezone for this campaign.") from exc
        if self.start_at is None and self.start_date is not None:
            self.start_at = datetime.combine(self.start_date, time.min)
        if self.end_at is None and self.end_date is not None:
            self.end_at = datetime.combine(self.end_date, time.max.replace(microsecond=0))
        if self.start_at is None or self.end_at is None:
            raise ValueError("Provide campaign start and end date and time.")
        if self.start_at.tzinfo is None:
            self.start_at = _resolve_local_time(self.start_at, campaign_zone)
        if self.end_at.tzinfo is None:
            self.end_at = _resolve_local_time(self.end_at, campaign_zone)
        if self.end_at <= self.start_at:
            raise ValueError("Campaign end time must be after its start time.")
        self.start_date = self.start_at.astimezone(campaign_zone).date()
        self.end_date = self.end_at.astimezone(campaign_zone).date()
        if self.daily_send_start is None:
            self.daily_send_start = time(9, 0)
        if self.daily_send_end is None:
            self.daily_send_end = time(17, 0)
        if self.daily_send_end <= self.daily_send_start:
            raise ValueError("Daily sending end time must be later than its start time")
        return self


class CampaignUpdate(CampaignCreate):
    """Full replacement of editable campaign setup before its first run."""


def _resolve_local_time(value: datetime, zone: ZoneInfo) -> datetime:
    """Interpret datetime-local values in the supplied IANA zone; reject DST gaps/folds."""
    candidate = value.replace(tzinfo=zone)
    round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != value:
        raise ValueError("This local time does not exist in the selected timezone because of a clock change.")
    other_fold = value.replace(tzinfo=zone, fold=1)
    if candidate.utcoffset() != other_fold.utcoffset():
        raise ValueError("This local time is ambiguous in the selected timezone because of a clock change.")
    return candidate


@router.post("")
async def create_campaign_endpoint(payload: CampaignCreate, settings: Settings = Depends(get_settings)):
    campaign = payload.model_dump(mode="json", exclude={"contexts", "step_delays_minutes", "step_triggers"}) | {"status": "draft"}
    result = await create_campaign(settings, campaign, payload.contexts, payload.step_delays_minutes, payload.step_triggers)
    return result


@router.get("")
async def list_campaigns_endpoint(settings: Settings = Depends(get_settings)):
    return await list_campaigns(settings)


@router.patch("/{campaign_id}")
async def update_campaign_endpoint(campaign_id: UUID, payload: CampaignUpdate,
                                   settings: Settings = Depends(get_settings)):
    campaign = payload.model_dump(mode="json", exclude={"contexts", "step_delays_minutes", "step_triggers"})
    return await update_campaign(settings, str(campaign_id), campaign, payload.contexts,
                                 payload.step_delays_minutes, payload.step_triggers)


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
