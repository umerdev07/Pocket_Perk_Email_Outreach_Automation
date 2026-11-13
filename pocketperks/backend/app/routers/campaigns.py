from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo
from uuid import UUID

from app import campaigns_postgres

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
        if self.step_triggers is None:
            self.step_triggers = ["scheduled"] * self.total_emails
        if self.step_delays_minutes is None:
            self.step_delays_minutes = [0, *([4320] * (self.total_emails - 1))]
        if len(self.step_delays_minutes) != self.total_emails:
            raise ValueError("step_delays_minutes must contain one delay per email step")
        if self.step_delays_minutes[0] != 0 or any(type(delay) is not int or delay < (0 if i == 0 else 15) or delay > 43200
                                                   for i, delay in enumerate(self.step_delays_minutes)):
            raise ValueError("Email 1 must have no delay; follow-up delays must be valid")
        if (len(self.step_triggers) != self.total_emails or self.step_triggers[0] != "scheduled"
                or (len(self.step_triggers) > 1 and self.step_triggers[1] != "scheduled")
                or any(trigger not in {"scheduled", "after_conversation", "close_loop"} for trigger in self.step_triggers)):
            raise ValueError("Emails 1 and 2 must be scheduled; later steps can be timed, close-loop, or after_conversation.")
        if any(trigger == "scheduled" and index > 0 and self.step_delays_minutes[index] != 4320
               for index, trigger in enumerate(self.step_triggers)):
            raise ValueError("Automatic follow-ups must be scheduled exactly 3 days after the previous email.")
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


async def _campaign_call(request: Request, function: str, *args):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Campaign storage is temporarily unavailable.")
    return await getattr(campaigns_postgres, function)(pool, *args)


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
async def create_campaign_endpoint(payload: CampaignCreate, request: Request):
    campaign = payload.model_dump(mode="json", exclude={"contexts", "step_delays_minutes", "step_triggers"}) | {"status": "draft"}
    result = await _campaign_call(request, "create_campaign", campaign, payload.contexts,
                                  payload.step_delays_minutes, payload.step_triggers)
    return result


@router.get("")
async def list_campaigns_endpoint(request: Request):
    return await _campaign_call(request, "list_campaigns")


@router.patch("/{campaign_id}")
async def update_campaign_endpoint(campaign_id: UUID, payload: CampaignUpdate, request: Request):
    campaign = payload.model_dump(mode="json", exclude={"contexts", "step_delays_minutes", "step_triggers"})
    return await _campaign_call(request, "update_campaign", str(campaign_id), campaign,
                                payload.contexts, payload.step_delays_minutes, payload.step_triggers)


@router.get("/{campaign_id}")
async def get_campaign_endpoint(campaign_id: str, request: Request):
    result = await _campaign_call(request, "get_campaign", campaign_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return result


@router.delete("/{campaign_id}/steps/{step_id}")
async def delete_campaign_step_endpoint(campaign_id: UUID, step_id: UUID, request: Request):
    return await _campaign_call(request, "delete_campaign_step", str(campaign_id), str(step_id))


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign_endpoint(campaign_id: UUID, request: Request):
    await _campaign_call(request, "delete_campaign", str(campaign_id))
