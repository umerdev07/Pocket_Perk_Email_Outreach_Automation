import asyncio
from unittest.mock import patch

import pytest

from app.config import Settings
from app import instantly_sync_db as sync_db


def cfg():
    return Settings(_env_file=None, supabase_url="https://db.example.test",
                   supabase_service_key="service-test")


class Response:
    def __init__(self, rows, status=200):
        self.rows = rows
        self.status_code = status
        self.is_success = 200 <= status < 300

    def json(self):
        return self.rows


class FakeSupabase:
    outcomes, requests = [], []

    def __init__(self, **kwargs):
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, params, headers, json):
        self.requests.append(("POST", url, params, headers, json))
        outcome = self.outcomes.pop(0)
        return outcome

    async def get(self, url, *, params, headers):
        self.requests.append(("GET", url, params, headers, None))
        outcome = self.outcomes.pop(0)
        return outcome

    async def patch(self, url, *, params, headers, json):
        self.requests.append(("PATCH", url, params, headers, json))
        outcome = self.outcomes.pop(0)
        return outcome


def reset(*outcomes):
    FakeSupabase.outcomes, FakeSupabase.requests = list(outcomes), []


def test_campaign_mapping_create_is_idempotent_and_uses_run_id_conflict_key():
    row = {"id": "mapping-1", "run_id": "run-1", "status": "pending"}
    reset(Response([row], 201))
    with patch.object(sync_db.httpx, "AsyncClient", FakeSupabase):
        result = asyncio.run(sync_db.create_campaign_mapping(cfg(), "run-1"))

    assert result == row
    method, url, params, headers, payload = FakeSupabase.requests[0]
    assert method == "POST"
    assert url.endswith("/instantly_campaign_mappings")
    assert params == {"on_conflict": "run_id"}
    assert "resolution=ignore-duplicates" in headers["Prefer"]
    assert payload == {"run_id": "run-1"}


def test_campaign_mapping_reloads_existing_row_after_idempotent_conflict():
    row = {"id": "mapping-1", "run_id": "run-1", "status": "pending"}
    reset(Response([]), Response([row]))
    with patch.object(sync_db.httpx, "AsyncClient", FakeSupabase):
        result = asyncio.run(sync_db.create_campaign_mapping(cfg(), "run-1"))

    assert result == row
    assert [request[0] for request in FakeSupabase.requests] == ["POST", "GET"]


def test_lead_mappings_are_written_in_bounded_chunks():
    rows = [{"lead_id": f"lead-{index}", "status": "synced", "instantly_lead_id": f"provider-{index}"}
            for index in range(sync_db.DB_BATCH_SIZE + 1)]
    reset(Response([{} for _ in range(sync_db.DB_BATCH_SIZE)]), Response([{}]))
    with patch.object(sync_db.httpx, "AsyncClient", FakeSupabase):
        count = asyncio.run(sync_db.save_lead_mappings(cfg(), "mapping-1", rows))

    assert count == sync_db.DB_BATCH_SIZE + 1
    assert [len(request[4]) for request in FakeSupabase.requests] == [sync_db.DB_BATCH_SIZE, 1]


def test_synced_mapping_requires_provider_id_before_database_call():
    reset()
    with patch.object(sync_db.httpx, "AsyncClient", FakeSupabase):
        with pytest.raises(ValueError, match="requires an Instantly lead ID"):
            asyncio.run(sync_db.save_lead_mappings(cfg(), "mapping-1", [
                {"lead_id": "lead-1", "status": "synced"}
            ]))
    assert FakeSupabase.requests == []


def test_campaign_mapping_rejects_unknown_update_fields():
    reset()
    with patch.object(sync_db.httpx, "AsyncClient", FakeSupabase):
        with pytest.raises(ValueError, match="Only provider campaign ID"):
            asyncio.run(sync_db.update_campaign_mapping(cfg(), "mapping-1", {"token": "secret"}))
    assert FakeSupabase.requests == []
