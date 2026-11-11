"""Admin-only suppression list and audited re-subscription controls."""

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Query, Request

from app.config import Settings, get_settings
from app.suppression_db import list_suppression_management, release_suppression

router = APIRouter(prefix="/api/suppressions", tags=["email suppression"])


class ReleaseRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=300)
    recipient_confirmed: bool = False
    bounce_address_corrected: bool = False


@router.get("")
async def get_suppression_management(limit: int = Query(default=100, ge=1, le=100),
                                    offset: int = Query(default=0, ge=0),
                                    settings: Settings = Depends(get_settings)):
    return await list_suppression_management(settings, limit=limit, offset=offset)


@router.post("/{email}/release")
async def release_email_address(email: str, body: ReleaseRequest, request: Request,
                                settings: Settings = Depends(get_settings)):
    return await release_suppression(settings, email, body.reason,
                                     request.state.workspace_user["id"],
                                     recipient_confirmed=body.recipient_confirmed,
                                     bounce_address_corrected=body.bounce_address_corrected)
