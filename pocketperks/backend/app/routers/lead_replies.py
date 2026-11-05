"""PocketPerks Lead Replies page API."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.instantly_replies_service import sync_recent_replies
from app.replies_db import list_replies, mark_reply_opt_out, mark_reply_read

router = APIRouter(prefix="/api/lead-replies", tags=["lead replies"])


@router.get("")
async def get_lead_replies(limit: int = Query(default=100, ge=1, le=100),
                           offset: int = Query(default=0, ge=0),
                           settings: Settings = Depends(get_settings)):
    return await list_replies(settings, limit=limit, offset=offset)


@router.post("/sync")
async def refresh_lead_replies(pages: int = Query(default=1, ge=1, le=5),
                               settings: Settings = Depends(get_settings)):
    return await sync_recent_replies(settings, max_pages=pages)


@router.patch("/{reply_id}/read")
async def read_lead_reply(reply_id: UUID, settings: Settings = Depends(get_settings)):
    # This updates PocketPerks' local review state only; Instantly's Unibox state is untouched.
    return await mark_reply_read(settings, str(reply_id))


@router.post("/{reply_id}/opt-out")
async def opt_out_lead_reply(reply_id: UUID, settings: Settings = Depends(get_settings)):
    return await mark_reply_opt_out(settings, str(reply_id))
