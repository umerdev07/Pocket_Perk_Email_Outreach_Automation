from app.config import Settings
from app.postgres import check_postgres


async def check_database(settings: Settings) -> bool:
    """Check that the configured PostgreSQL database is reachable."""
    return await check_postgres(settings.database_url)
