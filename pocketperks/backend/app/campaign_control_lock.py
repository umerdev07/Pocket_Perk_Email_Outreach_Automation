"""Serialize scheduled activation and user pause/resume for each provider run."""

from contextlib import asynccontextmanager


@asynccontextmanager
async def campaign_control_lock(pool, mapping_id: str):
    key = f"pocketperks-instantly-control:{mapping_id}"
    async with pool.acquire() as conn:
        await conn.fetchval("select pg_advisory_lock(hashtextextended($1, 0))", key)
        try:
            yield conn
        finally:
            await conn.fetchval("select pg_advisory_unlock(hashtextextended($1, 0))", key)
