import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.config import Settings
from app.campaigns_db import get_campaign


def test_context_count_mismatch_is_rejected():
    with patch("app.routers.campaigns.create_campaign", new=AsyncMock()) as save:
        response = TestClient(create_app(auth_enabled=False)).post("/api/campaigns", json={
            "name": "Cafe outreach", "total_emails": 2,
            "instructions": "Introduce loyalty stamps", "start_date": "2026-11-01", "end_date": "2026-11-30", "contexts": ["First email"],
        })
    assert response.status_code == 422
    save.assert_not_awaited()


def test_email_count_outside_bounds_is_rejected():
    client = TestClient(create_app(auth_enabled=False))
    for count in (0, 11):
        response = client.post("/api/campaigns", json={
            "name": "Cafe outreach", "total_emails": count,
            "instructions": "Introduce loyalty stamps", "start_date": "2026-11-01", "end_date": "2026-11-30", "contexts": ["Step"] * count,
        })
        assert response.status_code == 422


def test_valid_campaign_and_steps_are_saved_through_database_module():
    expected = {"id": "campaign-1", "status": "draft", "steps": [
        {"step_number": 1, "context": "Introduction", "delay_days": 0},
        {"step_number": 2, "context": "Follow-up", "delay_days": 3},
    ]}
    with patch("app.routers.campaigns.create_campaign", new=AsyncMock(return_value=expected)) as save:
        response = TestClient(create_app(auth_enabled=False)).post("/api/campaigns", json={
            "name": "Cafe outreach", "total_emails": 2,
            "instructions": "Introduce loyalty stamps", "start_date": "2026-11-01", "end_date": "2026-11-30", "contexts": ["Introduction", "Follow-up"],
        })
    assert response.status_code == 200
    assert response.json() == expected
    assert len(save.await_args.args[2]) == 2
    assert save.await_args.args[1]["start_date"] == "2026-11-01"
    assert save.await_args.args[1]["end_date"] == "2026-11-30"


def test_campaign_rejects_non_three_day_automatic_followup():
    with patch("app.routers.campaigns.create_campaign", new=AsyncMock()) as save:
        response = TestClient(create_app(auth_enabled=False)).post("/api/campaigns", json={
            "name": "Cafe outreach", "total_emails": 2,
            "instructions": "Introduce loyalty stamps", "start_date": "2026-11-01",
            "end_date": "2026-11-30", "contexts": ["Introduction", "Follow-up"],
            "step_delays_minutes": [0, 240],
        })
    assert response.status_code == 422
    save.assert_not_awaited()


def test_campaign_end_date_before_start_date_is_rejected():
    with patch("app.routers.campaigns.create_campaign", new=AsyncMock()) as save:
        response = TestClient(create_app(auth_enabled=False)).post("/api/campaigns", json={
            "name": "Cafe outreach", "total_emails": 1,
            "instructions": "Introduce loyalty stamps", "start_date": "2026-11-10",
            "end_date": "2026-11-09", "contexts": ["Introduction"],
        })
    assert response.status_code == 422
    save.assert_not_awaited()


def test_campaign_detail_accepts_one_to_one_draft_object_from_postgrest():
    class Response:
        is_success = True
        def json(self):
            return [{"id": "c1", "status": "approved", "campaign_steps": [{
                "id": "s1", "step_number": 1, "context": "Intro", "delay_days": 0,
                "generic_drafts": {"id": "d1", "subject": "Hello", "body": "Hi there", "status": "approved"},
            }]}]
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs): return Response()
    from unittest.mock import patch
    config = Settings(_env_file=None, supabase_url="https://example.supabase.co", supabase_service_key="test-key")
    with patch("app.campaigns_db.httpx.AsyncClient", FakeClient):
        result = asyncio.run(get_campaign(config, "c1"))
    assert result["steps"][0]["draft"]["id"] == "d1"
    assert result["steps"][0]["draft"]["status"] == "approved"


def test_delete_campaign_endpoint_deletes_through_database_module():
    campaign_id = "66e8dff4-94de-42eb-9e2c-5def2dbde0cb"
    with patch("app.routers.campaigns.delete_campaign", new=AsyncMock()) as delete:
        response = TestClient(create_app(auth_enabled=False)).delete(f"/api/campaigns/{campaign_id}")
    assert response.status_code == 204
    delete.assert_awaited_once()
    assert delete.await_args.args[1] == campaign_id


def test_delete_campaign_step_endpoint_uses_database_module():
    campaign_id = "66e8dff4-94de-42eb-9e2c-5def2dbde0cb"
    step_id = "9be8dff4-94de-42eb-9e2c-5def2dbde0cb"
    expected = {"deleted": True, "campaign_id": campaign_id, "total_emails": 1}
    with patch("app.routers.campaigns.delete_campaign_step", new=AsyncMock(return_value=expected)) as delete:
        response = TestClient(create_app(auth_enabled=False)).delete(f"/api/campaigns/{campaign_id}/steps/{step_id}")
    assert response.status_code == 200
    assert response.json() == expected
    delete.assert_awaited_once()
