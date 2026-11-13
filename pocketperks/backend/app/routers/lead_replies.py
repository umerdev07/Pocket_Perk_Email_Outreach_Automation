"""PocketPerks Lead Replies page API."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.instantly_replies_service import sync_recent_replies
from app.replies_db import delete_reply, list_replies, mark_reply_opt_out, mark_reply_read, send_reply

router = APIRouter(prefix="/api/lead-replies", tags=["lead replies"])


class ReplyBody(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)


@router.get("")
async def get_lead_replies(request: Request, limit: int = Query(default=100, ge=1, le=100),
                           offset: int = Query(default=0, ge=0),
                           settings: Settings = Depends(get_settings)):
    return await list_replies(settings, limit=limit, offset=offset,
                              pool=getattr(request.app.state,"postgres_pool",None))


@router.post("/sync")
async def refresh_lead_replies(request: Request, pages: int = Query(default=1, ge=1, le=5),
                               settings: Settings = Depends(get_settings)):
    return await sync_recent_replies(settings, max_pages=pages,pool=getattr(request.app.state,"postgres_pool",None))


@router.patch("/{reply_id}/read")
async def read_lead_reply(reply_id: UUID, request: Request, settings: Settings = Depends(get_settings)):
    # This updates PocketPerks' local review state only; Instantly's Unibox state is untouched.
    return await mark_reply_read(settings, str(reply_id),pool=getattr(request.app.state,"postgres_pool",None))


@router.post("/{reply_id}/reply")
async def reply_to_lead_email(reply_id: UUID, payload: ReplyBody, request: Request,
                              settings: Settings = Depends(get_settings)):
    return await send_reply(settings, str(reply_id), payload.body,pool=getattr(request.app.state,"postgres_pool",None))


@router.delete("/{reply_id}")
async def delete_lead_reply(reply_id: UUID, request: Request, settings: Settings = Depends(get_settings)):
    return await delete_reply(settings, str(reply_id),pool=getattr(request.app.state,"postgres_pool",None))


@router.post("/{reply_id}/opt-out")
async def opt_out_lead_reply(reply_id: UUID, request: Request, settings: Settings = Depends(get_settings)):
    return await mark_reply_opt_out(settings, str(reply_id), request.state.workspace_user["id"],pool=getattr(request.app.state,"postgres_pool",None))
