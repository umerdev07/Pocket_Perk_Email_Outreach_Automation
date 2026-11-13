import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app import instantly_sync_db
from app.config import Settings


settings = Settings(_env_file=None)


def test_campaign_mapping_uses_postgresql_repository():
    pool, expected = object(), {"id": "mapping-1", "run_id": "run-1"}
    with patch("app.instantly_sync_postgres.create_campaign_mapping", new=AsyncMock(return_value=expected)) as create:
        result = asyncio.run(instantly_sync_db.create_campaign_mapping(
            settings, "run-1", ["sender@example.test"], pool=pool))
    assert result == expected
    create.assert_awaited_once_with(pool, "run-1", ["sender@example.test"])


def test_mapping_operations_fail_closed_without_postgres():
    with pytest.raises(HTTPException) as raised:
        asyncio.run(instantly_sync_db.get_campaign_mapping_for_run(settings, "run-1"))
    assert raised.value.status_code == 503


def test_synced_mapping_requires_provider_id():
    with pytest.raises(ValueError, match="requires an Instantly lead ID"):
        asyncio.run(instantly_sync_db.save_lead_mappings(settings, "mapping-1", [
            {"lead_id": "lead-1", "status": "synced"}
        ], pool=object()))


def test_mapping_update_rejects_unknown_fields_before_database_access():
    with pytest.raises(ValueError, match="Only provider campaign ID"):
        asyncio.run(instantly_sync_db.update_campaign_mapping(
            settings, "mapping-1", {"token": "secret"}, pool=object()))
