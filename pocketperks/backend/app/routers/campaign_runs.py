from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from app.campaign_runs_db import get_campaign_run, start_campaign_run
from app.config import Settings, get_settings
from app.personalization_db import get_run_lead_personalized_emails, get_run_personalized_emails
from app.services.personalization_service import process_campaign_run

router = APIRouter(prefix="/api/campaigns", tags=["campaign runs"])
runs_router = APIRouter(prefix="/api/campaign-runs", tags=["campaign runs"])


class StartCampaignRequest(BaseModel):
    batch_id: UUID


@router.post("/{campaign_id}/start")
async def start_campaign(campaign_id: UUID, payload: StartCampaignRequest, background_tasks: BackgroundTasks,
                         settings: Settings = Depends(get_settings)):
    result = await start_campaign_run(settings, str(campaign_id), str(payload.batch_id))
    background_tasks.add_task(process_campaign_run, settings, result["run_id"])
    return result


@runs_router.get("/{run_id}")
async def campaign_run_progress(run_id: UUID, settings: Settings = Depends(get_settings)):
    result = await get_campaign_run(settings, str(run_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Campaign run not found.")
    return result


@runs_router.get("/{run_id}/emails")
async def campaign_run_emails(run_id: UUID, limit: int = Query(default=30, ge=1, le=100),
                              offset: int = Query(default=0, ge=0),
                              settings: Settings = Depends(get_settings)):
    return await get_run_personalized_emails(settings, str(run_id), limit=limit, offset=offset)


@runs_router.get("/{run_id}/leads/{lead_id}/emails")
async def campaign_run_lead_emails(run_id: UUID, lead_id: UUID, settings: Settings = Depends(get_settings)):
    return await get_run_lead_personalized_emails(settings, str(run_id), str(lead_id))
