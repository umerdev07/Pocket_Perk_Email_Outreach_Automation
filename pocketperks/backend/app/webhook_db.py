"""Durable queue for provider webhooks, deduplicated by raw payload digest."""
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException

from app.config import Settings


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for provider webhooks.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def enqueue_instantly_webhook(settings: Settings, event_key: str, payload: dict) -> bool:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{_base(settings)}/instantly_webhook_events",
            params={"on_conflict": "event_key"}, headers=_headers(settings, "resolution=ignore-duplicates,return=representation"),
            json={"event_key": event_key, "payload": payload})
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Webhook could not be safely queued. Retry delivery.")
    try:
        rows = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Webhook queue confirmation was invalid.") from exc
    if not isinstance(rows, list):
        raise HTTPException(status_code=503, detail="Webhook queue confirmation was invalid.")
    return bool(rows)


async def claim_instantly_webhooks(settings: Settings, limit: int = 10) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{_base(settings)}/rpc/claim_instantly_webhook_events",
            headers=_headers(settings), json={"p_limit": limit})
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not claim provider webhook events.")
    rows = response.json()
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise HTTPException(status_code=503, detail="Provider webhook queue response was invalid.")
    return rows


async def finish_instantly_webhook(settings: Settings, event_id: int, *, error: str | None = None,
                                   attempts: int = 1) -> None:
    values = {"status": "processed" if error is None else "pending" if attempts < 10 else "failed",
              "processed_at": datetime.now(UTC).isoformat() if error is None else None,
              "error_detail": error[:160] if error else None,
              "updated_at": datetime.now(UTC).isoformat()}
    if error is None:
        # Drop message contents after successful handling; keep only dedupe/audit metadata.
        values["payload"] = {}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(f"{_base(settings)}/instantly_webhook_events",
            params={"id": f"eq.{event_id}", "status": "eq.processing"},
            headers=_headers(settings, "return=representation"), json=values)
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not record provider webhook processing state.")
