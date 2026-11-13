"""Native PostgreSQL migration and connectivity helpers."""

from pathlib import Path
import json

import asyncpg
from fastapi import HTTPException, Request

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


async def _configure_connection(connection) -> None:
    # asyncpg otherwise returns json/jsonb as raw strings. Repositories and
    # queued job payloads intentionally expose decoded Python values.
    for type_name in ("json", "jsonb"):
        await connection.set_type_codec(type_name, schema="pg_catalog", encoder=json.dumps,
                                        decoder=json.loads, format="text")


async def open_pool(database_url: str) -> asyncpg.Pool:
    """Create a bounded application pool for native PostgreSQL repositories."""
    if not database_url.strip():
        raise ValueError("DATABASE_URL is required to connect to PostgreSQL.")
    return await asyncpg.create_pool(
        database_url,
        min_size=1,
        max_size=10,
        command_timeout=30,
        timeout=15,
        init=_configure_connection,
    )


async def check_pool(pool: asyncpg.Pool | None) -> bool:
    """Check the already-open application pool without creating a new connection."""
    if pool is None:
        return False
    try:
        async with pool.acquire() as connection:
            return await connection.fetchval("select 1") == 1
    except (asyncpg.PostgresError, OSError, TimeoutError):
        return False


def get_pool(request: Request) -> asyncpg.Pool:
    """FastAPI dependency for PostgreSQL-backed repository functions."""
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL storage is not configured.")
    return pool


async def apply_migrations(database_url: str, migrations_dir: Path | None = None) -> list[str]:
    """Apply unapplied numbered SQL files atomically and in filename order."""
    if not database_url.strip():
        raise ValueError("DATABASE_URL is required to apply PostgreSQL migrations.")

    directory = migrations_dir or _MIGRATIONS_DIR
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    applied: list[str] = []
    connection = await asyncpg.connect(database_url, timeout=15)
    try:
        await connection.execute("""
            create table if not exists public.schema_migrations (
                version text primary key,
                applied_at timestamptz not null default now()
            )
        """)
        for path in files:
            version = path.stem
            async with connection.transaction():
                already_applied = await connection.fetchval(
                    "select exists(select 1 from public.schema_migrations where version = $1)", version
                )
                if already_applied:
                    continue
                await connection.execute(path.read_text(encoding="utf-8"))
                await connection.execute(
                    "insert into public.schema_migrations(version) values ($1)", version
                )
                applied.append(version)
    finally:
        await connection.close()
    return applied


async def check_postgres(database_url: str) -> bool:
    """Return whether the configured PostgreSQL endpoint answers a basic query."""
    if not database_url.strip():
        return False
    connection = None
    try:
        connection = await asyncpg.connect(database_url, timeout=5)
        return await connection.fetchval("select 1") == 1
    except (asyncpg.PostgresError, OSError, TimeoutError):
        return False
    finally:
        if connection is not None:
            await connection.close()
