from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.dashboard_db import get_dashboard_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(settings: Settings = Depends(get_settings)):
    return await get_dashboard_summary(settings)
