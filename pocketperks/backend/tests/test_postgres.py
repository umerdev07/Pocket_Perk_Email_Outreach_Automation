import asyncio

import pytest

from app.postgres import apply_migrations, check_pool, check_postgres, open_pool


def test_postgres_health_is_false_when_database_url_is_missing():
    assert asyncio.run(check_postgres("")) is False


def test_postgres_migrations_refuse_to_run_without_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        asyncio.run(apply_migrations(""))


def test_native_postgres_pool_requires_a_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        asyncio.run(open_pool(""))


def test_native_postgres_pool_health_is_false_without_a_pool():
    assert asyncio.run(check_pool(None)) is False
