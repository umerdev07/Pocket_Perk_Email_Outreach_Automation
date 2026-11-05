"""Server-side access to the private Supabase PGMQ worker functions."""
import httpx
from fastapi import HTTPException

from app.config import Settings


def _rpc_url(settings: Settings, function_name: str) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database queue is not configured.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1/rpc/{function_name}"


def _headers(settings: Settings) -> dict[str, str]:
    key = settings.supabase_service_key
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def read_personalization_jobs(settings: Settings, *, visibility_seconds: int = 120,
                                   quantity: int = 1) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(_rpc_url(settings, "read_personalization_jobs"),
                headers=_headers(settings), json={"visibility_seconds": visibility_seconds, "quantity": quantity})
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not reach the durable personalization queue.") from exc
    if not response.is_success:
        raise RuntimeError("Could not read the durable personalization queue.")
    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError("The durable personalization queue returned an invalid response.")
    return data


async def extend_personalization_job(settings: Settings, message_id: int, *, visibility_seconds: int = 120) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(_rpc_url(settings, "extend_personalization_job"),
                headers=_headers(settings),
                json={"target_message_id": message_id, "visibility_seconds": visibility_seconds})
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not renew the personalization queue lease.") from exc
    if not response.is_success:
        raise RuntimeError("Could not renew the personalization queue lease.")
    return response.json() is True


async def archive_personalization_job(settings: Settings, message_id: int) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(_rpc_url(settings, "archive_personalization_job"),
                headers=_headers(settings), json={"target_message_id": message_id})
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not archive the completed personalization job.") from exc
    if not response.is_success:
        raise RuntimeError("Could not archive the completed personalization job.")
    return response.json() is True


async def reset_run_for_retry(settings: Settings, run_id: str) -> None:
    """Reset progress after a fatal attempt; saved emails remain idempotently reusable."""
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for campaign runs.")
    key = settings.supabase_service_key
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/campaign_runs",
            params={"id": f"eq.{run_id}"},
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"status": "running", "processed": 0, "skipped": 0, "failed": 0,
                  "finished_at": None, "error_detail": None, "skip_log": [], "failure_log": []},
        )
    if not response.is_success:
        raise RuntimeError("Could not prepare the campaign run for a safe retry.")


async def read_instantly_sync_jobs(settings: Settings, *, visibility_seconds: int = 300,
                                  quantity: int = 1) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(_rpc_url(settings, "read_instantly_sync_jobs"),
                headers=_headers(settings), json={"visibility_seconds": visibility_seconds, "quantity": quantity})
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not reach the durable Instantly sync queue.") from exc
    if not response.is_success or not isinstance(response.json(), list):
        raise RuntimeError("Could not read the durable Instantly sync queue.")
    return response.json()


async def extend_instantly_sync_job(settings: Settings, message_id: int, *, visibility_seconds: int = 300) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(_rpc_url(settings, "extend_instantly_sync_job"),
                headers=_headers(settings), json={"target_message_id": message_id,
                                                  "visibility_seconds": visibility_seconds})
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not renew the Instantly sync queue lease.") from exc
    if not response.is_success:
        raise RuntimeError("Could not renew the Instantly sync queue lease.")
    return response.json() is True


async def archive_instantly_sync_job(settings: Settings, message_id: int) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(_rpc_url(settings, "archive_instantly_sync_job"),
                headers=_headers(settings), json={"target_message_id": message_id})
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not archive the Instantly sync queue job.") from exc
    if not response.is_success:
        raise RuntimeError("Could not archive the Instantly sync queue job.")
    return response.json() is True
