"""Persistence helpers for generic campaign drafts."""
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException

from app.config import Settings


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for drafts.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def load_campaign(settings: Settings, campaign_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_base(settings)}/campaigns", params={
            "select": "id,name,instructions,status,campaign_steps(id,step_number,context,generic_drafts(id,subject,body,status))",
            "id": f"eq.{campaign_id}", "limit": "1",
        }, headers=_headers(settings))
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load the campaign for draft generation.")
    rows = response.json()
    return rows[0] if rows else None


async def upsert_step_draft(settings: Settings, step_id: str, subject: str, body: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{_base(settings)}/generic_drafts", params={"on_conflict": "step_id"},
            headers=_headers(settings, "resolution=merge-duplicates,return=representation"),
            json={"step_id": step_id, "subject": subject, "body": body, "status": "generated", "approved_at": None})
    if not response.is_success or not response.json():
        raise HTTPException(status_code=503, detail="Could not save the generated draft.")
    return response.json()[0]


async def get_draft_context(settings: Settings, draft_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_base(settings)}/generic_drafts", params={
            "select": "id,step_id,subject,body,status,campaign_steps(id,campaign_id,step_number,context,campaigns(id,name,instructions,status))",
            "id": f"eq.{draft_id}", "limit": "1",
        }, headers=_headers(settings))
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load the draft.")
    rows = response.json()
    return rows[0] if rows else None


async def update_draft(settings: Settings, draft_id: str, subject: str, body: str) -> dict:
    previous = await get_draft_context(settings, draft_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(f"{_base(settings)}/generic_drafts", params={"id": f"eq.{draft_id}"},
            headers=_headers(settings, "return=representation"),
            json={"subject": subject, "body": body, "status": "edited", "approved_at": None})
        if previous.get("status") == "approved":
            rel = previous.get("campaign_steps") or {}
            campaign_id = rel.get("campaign_id")
            if campaign_id:
                await client.patch(f"{_base(settings)}/campaigns", params={"id": f"eq.{campaign_id}"},
                    headers=_headers(settings), json={"status": "draft"})
    if not response.is_success or not response.json():
        raise HTTPException(status_code=503, detail="Could not update the draft.")
    return response.json()[0]


async def approve_draft(settings: Settings, draft_id: str) -> dict:
    previous = await get_draft_context(settings, draft_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(f"{_base(settings)}/generic_drafts", params={"id": f"eq.{draft_id}"},
            headers=_headers(settings, "return=representation"),
            json={"status": "approved", "approved_at": datetime.now(UTC).isoformat()})
        if not response.is_success or not response.json():
            raise HTTPException(status_code=503, detail="Could not approve the draft.")
        step = previous.get("campaign_steps") or {}
        campaign_id = step.get("campaign_id")
        if campaign_id:
            campaign = await load_campaign(settings, campaign_id)
            steps = (campaign or {}).get("campaign_steps", [])
            def draft_status(step: dict) -> str | None:
                relation = step.get("generic_drafts")
                if isinstance(relation, list):
                    relation = relation[0] if relation else None
                return relation.get("status") if isinstance(relation, dict) else None

            if steps and all(draft_status(step) == "approved" for step in steps):
                await client.patch(f"{_base(settings)}/campaigns", params={"id": f"eq.{campaign_id}"},
                    headers=_headers(settings), json={"status": "approved"})
    return response.json()[0]
