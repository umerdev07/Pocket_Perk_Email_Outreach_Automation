from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.dashboard_db import get_dashboard_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(days: int = Query(default=14, ge=7, le=90),
                            settings: Settings = Depends(get_settings)):
    return await get_dashboard_summary(settings, activity_days=days)
