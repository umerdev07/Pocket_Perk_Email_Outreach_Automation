from fastapi import APIRouter, Depends, Query, Request
from uuid import UUID

from app.config import Settings, get_settings
from app.dashboard_db import get_dashboard_run_detail, get_dashboard_summary
from app.services.instantly_campaign_lifecycle import reconcile_deleted_provider_campaigns

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(request: Request, days: int = Query(default=14, ge=7, le=90),
                            settings: Settings = Depends(get_settings)):
    pool = getattr(request.app.state, "postgres_pool", None)
    # Best-effort provider reconciliation makes manually deleted drafts disappear
    # from the dashboard on refresh. Transient Instantly errors leave local data intact.
    await reconcile_deleted_provider_campaigns(settings, pool=pool, limit=5)
    return await get_dashboard_summary(settings, activity_days=days,
                                       pool=pool)


@router.get("/runs/{run_id}")
async def dashboard_run_detail(run_id: UUID, request: Request,
                               settings: Settings = Depends(get_settings)):
    return await get_dashboard_run_detail(settings, str(run_id),
                                          pool=getattr(request.app.state, "postgres_pool", None))
