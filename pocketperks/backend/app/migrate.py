"""CLI: python -m app.migrate"""

import asyncio

from app.config import get_settings
from app.postgres import apply_migrations


async def _run() -> None:
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required. No migrations were run.")
    applied = await apply_migrations(settings.database_url)
    if applied:
        print("Applied PostgreSQL migrations: " + ", ".join(applied))
    else:
        print("PostgreSQL schema is already up to date.")


if __name__ == "__main__":
    asyncio.run(_run())
