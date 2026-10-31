import httpx

from app.config import Settings


async def check_database(settings: Settings) -> bool:
    """Check access to the migrated table through Supabase's REST API."""
    if not settings.supabase_url or not settings.supabase_service_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{settings.supabase_url.rstrip('/')}/rest/v1/lead_batches",
                params={"select": "id", "limit": "1"},
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                },
            )
        return response.is_success and isinstance(response.json(), list)
    except (httpx.HTTPError, ValueError):
        return False
