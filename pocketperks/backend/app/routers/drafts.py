from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.drafts_db import (approve_draft, demote_campaign, get_draft_context, load_campaign,
                           prepare_step_draft, set_generation_state, update_draft)
from app.services.draft_service import (queue_campaign_draft_generation,
                                        run_campaign_draft_generation_job,
                                        run_regeneration_job, run_step_generation_job)

router = APIRouter(tags=["drafts"])


class DraftEdit(BaseModel):
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


@router.post("/api/campaigns/{campaign_id}/drafts/generate", status_code=202)
async def generate(campaign_id: str, background_tasks: BackgroundTasks,
                   settings: Settings = Depends(get_settings)):
    result = await queue_campaign_draft_generation(settings, campaign_id)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Campaign not found.")
    background_tasks.add_task(run_campaign_draft_generation_job, settings, campaign_id)
    return result


@router.patch("/api/drafts/{draft_id}")
async def edit(draft_id: str, payload: DraftEdit, settings: Settings = Depends(get_settings)):
    return await update_draft(settings, draft_id, payload.subject.strip(), payload.body.strip())


@router.post("/api/drafts/{draft_id}/regenerate", status_code=202)
async def regenerate(draft_id: str, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings)):
    draft = await get_draft_context(settings, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.get("generation_status") == "generating" and not _generation_is_stale(draft):
        return {"id": draft_id, "generation_status": "generating"}
    if draft.get("generation_status") == "generating":
        await set_generation_state(settings, draft_id, "failed", "Previous generation expired. Retry to start a fresh generation.")
    step = draft.get("campaign_steps") or {}
    campaign = step.get("campaigns") or {}
    if campaign.get("status") == "approved":
        await demote_campaign(settings, campaign.get("id") or step.get("campaign_id"))
    await set_generation_state(settings, draft_id, "generating")
    background_tasks.add_task(run_regeneration_job, settings, draft_id)
    return {"id": draft_id, "generation_status": "generating"}


@router.post("/api/campaigns/{campaign_id}/steps/{step_id}/regenerate", status_code=202)
async def regenerate_step(campaign_id: str, step_id: str, background_tasks: BackgroundTasks,
                          settings: Settings = Depends(get_settings)):
    campaign = await load_campaign(settings, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    step = next((item for item in campaign.get("campaign_steps", []) if item.get("id") == step_id), None)
    if step is None:
        raise HTTPException(status_code=404, detail="Campaign email step not found.")
    relation = step.get("generic_drafts")
    if isinstance(relation, list):
        relation = relation[0] if relation else None
    if isinstance(relation, dict) and relation.get("generation_status") == "generating":
        if not _generation_is_stale(relation):
            return {"id": relation["id"], "generation_status": "generating"}
        await set_generation_state(settings, relation["id"], "failed",
                                   "Previous generation expired. Retry to start a fresh generation.")
    draft = await prepare_step_draft(settings, step_id)
    if campaign.get("status") == "approved":
        await demote_campaign(settings, campaign_id)
    background_tasks.add_task(run_step_generation_job, settings, campaign_id, step_id, draft["id"])
    return {"id": draft["id"], "generation_status": "generating"}


def _generation_is_stale(draft: dict) -> bool:
    value = draft.get("generation_started_at")
    if not isinstance(value, str):
        return True
    try:
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if started.tzinfo is None:
            return True
    except ValueError:
        return True
    return datetime.now(UTC) - started.astimezone(UTC) > timedelta(minutes=5)


@router.post("/api/drafts/{draft_id}/approve")
async def approve(draft_id: str, settings: Settings = Depends(get_settings)):
    return await approve_draft(settings, draft_id)
