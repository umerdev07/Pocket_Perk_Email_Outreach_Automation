from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.services.draft_service import (queue_campaign_draft_generation,
                                        run_campaign_draft_generation_job,
                                        run_regeneration_job, run_step_generation_job)


def _pool(request: Request):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    return pool


async def _db_call(request: Request, settings: Settings, name: str, *args):
    pool = _pool(request)
    from app import drafts_postgres
    return await getattr(drafts_postgres, name)(pool, *args)

router = APIRouter(tags=["drafts"])


class DraftEdit(BaseModel):
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


@router.post("/api/campaigns/{campaign_id}/drafts/generate", status_code=202)
async def generate(campaign_id: str, request: Request, background_tasks: BackgroundTasks,
                   settings: Settings = Depends(get_settings)):
    pool = _pool(request)
    result = await queue_campaign_draft_generation(settings, campaign_id, pool)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Campaign not found.")
    background_tasks.add_task(run_campaign_draft_generation_job, settings, campaign_id, pool)
    return result


@router.patch("/api/drafts/{draft_id}")
async def edit(draft_id: str, payload: DraftEdit, request: Request, settings: Settings = Depends(get_settings)):
    try:
        return await _db_call(request, settings, "update_draft", draft_id, payload.subject.strip(), payload.body.strip())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Draft not found.") from exc


@router.post("/api/drafts/{draft_id}/regenerate", status_code=202)
async def regenerate(draft_id: str, request: Request, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings)):
    pool = _pool(request)
    draft = await _db_call(request, settings, "get_draft_context", draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.get("generation_status") == "generating" and not _generation_is_stale(draft):
        return {"id": draft_id, "generation_status": "generating"}
    if draft.get("generation_status") == "generating":
        await _db_call(request, settings, "set_generation_state", draft_id, "failed", "Previous generation expired. Retry to start a fresh generation.")
    step = draft.get("campaign_steps") or {}
    campaign = step.get("campaigns") or {}
    if campaign.get("status") == "approved":
        campaign_id = campaign.get("id") or step.get("campaign_id")
        await pool.execute("update public.campaigns set status='draft' where id=$1::uuid", campaign_id)
    await _db_call(request, settings, "set_generation_state", draft_id, "generating")
    background_tasks.add_task(run_regeneration_job, settings, draft_id, pool)
    return {"id": draft_id, "generation_status": "generating"}


@router.post("/api/campaigns/{campaign_id}/steps/{step_id}/regenerate", status_code=202)
async def regenerate_step(campaign_id: str, step_id: str, request: Request, background_tasks: BackgroundTasks,
                          settings: Settings = Depends(get_settings)):
    pool = _pool(request)
    campaign = await _db_call(request, settings, "load_campaign", campaign_id)
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
        await _db_call(request, settings, "set_generation_state", relation["id"], "failed",
                                   "Previous generation expired. Retry to start a fresh generation.")
    draft = await _db_call(request, settings, "prepare_step_draft", step_id)
    if campaign.get("status") == "approved":
        await pool.execute("update public.campaigns set status='draft' where id=$1::uuid", campaign_id)
    background_tasks.add_task(run_step_generation_job, settings, campaign_id, step_id, draft["id"], pool)
    return {"id": draft["id"], "generation_status": "generating"}


def _generation_is_stale(draft: dict) -> bool:
    value = draft.get("generation_started_at")
    try:
        started = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if started.tzinfo is None:
            return True
    except (AttributeError, TypeError, ValueError):
        return True
    return datetime.now(UTC) - started.astimezone(UTC) > timedelta(minutes=5)


@router.post("/api/drafts/{draft_id}/approve")
async def approve(draft_id: str, request: Request, settings: Settings = Depends(get_settings)):
    try:
        return await _db_call(request, settings, "approve_draft", draft_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Draft not found.") from exc
