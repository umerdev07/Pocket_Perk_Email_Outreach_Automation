import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings
from app.campaign_runs_db import get_campaign_run, start_campaign_run
from app.main import create_app
from app.personalization_db import get_run_lead_personalized_emails, get_run_personalized_emails

client = TestClient(create_app())


class FakeResponse:
    def __init__(self, payload, success=True, headers=None):
        self.payload, self.is_success, self.headers = payload, success, headers or {}
    def json(self):
        return self.payload


class FakeSupabase:
    responses, calls = [], []
    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)
    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


def fake(responses):
    FakeSupabase.responses, FakeSupabase.calls = list(responses), []


def db_settings():
    return Settings(_env_file=None, supabase_url="https://example.supabase.co", supabase_service_key="test-secret")


def test_start_rejects_unapproved_campaign():
    campaign_id, batch_id = uuid4(), uuid4()
    fake([FakeResponse([{"id": str(campaign_id), "status": "draft"}])])
    with patch("app.campaign_runs_db.httpx.AsyncClient", FakeSupabase):
        try:
            asyncio.run(start_campaign_run(db_settings(), str(campaign_id), str(batch_id)))
            assert False, "Expected an unapproved campaign to be rejected"
        except HTTPException as exc:
            assert exc.status_code == 409
    assert len(FakeSupabase.calls) == 1


def test_start_rejects_missing_batch():
    campaign_id, batch_id = uuid4(), uuid4()
    fake([FakeResponse([{"id": str(campaign_id), "status": "approved"}]), FakeResponse([])])
    with patch("app.campaign_runs_db.httpx.AsyncClient", FakeSupabase):
        try:
            asyncio.run(start_campaign_run(db_settings(), str(campaign_id), str(batch_id)))
            assert False, "Expected a missing batch to be rejected"
        except HTTPException as exc:
            assert exc.status_code == 404
            assert exc.detail == "Lead batch not found."
    assert len(FakeSupabase.calls) == 2


def test_valid_start_returns_created_run_summary():
    campaign_id, batch_id = uuid4(), uuid4()
    expected = {"run_id": str(uuid4()), "status": "running", "total_leads": 120}
    run_id = expected["run_id"]
    fake([FakeResponse([{"id": str(campaign_id), "status": "approved"}]),
          FakeResponse([{"id": str(batch_id), "imported_count": 120}]),
          FakeResponse([{"id": run_id, "status": "running", "total_leads": 120}])])
    with patch("app.campaign_runs_db.httpx.AsyncClient", FakeSupabase):
        created = asyncio.run(start_campaign_run(db_settings(), str(campaign_id), str(batch_id)))
    assert created == expected
    with patch("app.routers.campaign_runs.start_campaign_run", new=AsyncMock(return_value=expected)) as start, \
         patch("app.routers.campaign_runs.process_campaign_run", new=AsyncMock()) as process:
        response = client.post(f"/api/campaigns/{campaign_id}/start", json={"batch_id": str(batch_id)})
    assert response.status_code == 200
    assert response.json() == expected
    assert start.await_args.args[1:] == (str(campaign_id), str(batch_id))
    process.assert_awaited_once()
    assert FakeSupabase.calls[-1][0] == "POST"
    assert FakeSupabase.calls[-1][2]["json"]["total_leads"] == 120


def test_invalid_batch_id_is_rejected_before_database_call():
    with patch("app.routers.campaign_runs.start_campaign_run", new=AsyncMock()) as start:
        response = client.post(f"/api/campaigns/{uuid4()}/start", json={"batch_id": "not-a-uuid"})
    assert response.status_code == 422
    start.assert_not_awaited()


def test_run_progress_endpoint_returns_counters():
    run_id = uuid4()
    expected = {"run_id": str(run_id), "status": "running", "total_leads": 12,
                "processed": 4, "skipped": 1, "failed": 0}
    with patch("app.routers.campaign_runs.get_campaign_run", new=AsyncMock(return_value=expected)) as get_run:
        response = client.get(f"/api/campaign-runs/{run_id}")
    assert response.status_code == 200
    assert response.json() == expected
    get_run.assert_awaited_once()


def test_get_campaign_run_returns_saved_failure_log():
    run_id = str(uuid4())
    failure_log = [{"business_name": "Cafe Example", "step_number": 2,
                    "reason": "OpenRouter rate limit persisted after retries."}]
    fake([FakeResponse([{"id": run_id, "campaign_id": "c1", "batch_id": "b1", "status": "completed",
                         "total_leads": 1, "processed": 0, "skipped": 0, "failed": 1,
                         "error_detail": None, "failure_log": failure_log,
                         "created_at": "2026-11-01T00:00:00Z", "finished_at": "2026-11-01T00:01:00Z"}])])
    with patch("app.campaign_runs_db.httpx.AsyncClient", FakeSupabase):
        result = asyncio.run(get_campaign_run(db_settings(), run_id))
    assert result["failure_log"] == failure_log


def test_run_emails_endpoint_returns_paged_personalized_emails():
    run_id = uuid4()
    expected = {"items": [{"id": str(uuid4()), "subject": "Hello", "body": "A short email",
                           "lead": {"business_name": "Cafe Name"},
                           "step": {"step_number": 1, "context": "Introduce the product"}}],
                "total": 31, "limit": 10, "offset": 10, "has_more": True}
    with patch("app.routers.campaign_runs.get_run_personalized_emails",
               new=AsyncMock(return_value=expected)) as get_emails:
        response = client.get(f"/api/campaign-runs/{run_id}/emails?limit=10&offset=10")
    assert response.status_code == 200
    assert response.json() == expected
    get_emails.assert_awaited_once()
    assert get_emails.await_args.kwargs == {"limit": 10, "offset": 10}


def test_run_emails_endpoint_validates_pagination():
    run_id = uuid4()
    with patch("app.routers.campaign_runs.get_run_personalized_emails", new=AsyncMock()) as get_emails:
        response = client.get(f"/api/campaign-runs/{run_id}/emails?limit=101")
    assert response.status_code == 422
    get_emails.assert_not_awaited()


def test_run_personalized_email_page_includes_lead_and_step_details():
    run_id, lead_id, step_id = str(uuid4()), str(uuid4()), str(uuid4())
    fake([FakeResponse([{"id": run_id}]),
          FakeResponse([{"id": "email-1", "lead_id": lead_id, "step_id": step_id,
                         "subject": "Hello", "body": "Email body", "status": "generated"}],
                       headers={"content-range": "0-0/1"}),
          FakeResponse([{"id": lead_id, "business_name": "Cafe Name", "email": "cafe@example.test"}]),
          FakeResponse([{"id": step_id, "step_number": 2, "context": "Follow up"}])])
    with patch("app.personalization_db.httpx.AsyncClient", FakeSupabase):
        result = asyncio.run(get_run_personalized_emails(db_settings(), run_id, limit=30, offset=0))

    assert result["total"] == 1
    assert result["has_more"] is False
    assert result["items"][0]["lead"]["business_name"] == "Cafe Name"
    assert result["items"][0]["step"] == {"id": step_id, "step_number": 2, "context": "Follow up"}


def test_run_lead_emails_endpoint_returns_all_steps_for_selected_lead():
    run_id, lead_id = uuid4(), uuid4()
    expected = {"lead": {"id": str(lead_id), "business_name": "Cafe Name"},
                "items": [{"id": "email-1", "step": {"step_number": 1}}]}
    with patch("app.routers.campaign_runs.get_run_lead_personalized_emails",
               new=AsyncMock(return_value=expected)) as get_emails:
        response = client.get(f"/api/campaign-runs/{run_id}/leads/{lead_id}/emails")
    assert response.status_code == 200
    assert response.json() == expected
    get_emails.assert_awaited_once()
    assert get_emails.await_args.args[1:] == (str(run_id), str(lead_id))


def test_run_lead_email_detail_loads_steps_and_sorts_them_by_number():
    run_id, lead_id = str(uuid4()), str(uuid4())
    step_2, step_1 = str(uuid4()), str(uuid4())
    fake([FakeResponse([
              {"id": "email-2", "lead_id": lead_id, "step_id": step_2, "subject": "Follow-up", "body": "Second"},
              {"id": "email-1", "lead_id": lead_id, "step_id": step_1, "subject": "Intro", "body": "First"},
          ]),
          FakeResponse([{"id": lead_id, "business_name": "Cafe Name", "email": "cafe@example.test"}]),
          FakeResponse([{"id": step_2, "step_number": 2, "context": "Follow up"},
                        {"id": step_1, "step_number": 1, "context": "Introduce"}])])
    with patch("app.personalization_db.httpx.AsyncClient", FakeSupabase):
        result = asyncio.run(get_run_lead_personalized_emails(db_settings(), run_id, lead_id))
    assert [item["step"]["step_number"] for item in result["items"]] == [1, 2]
    assert all(item["lead"]["business_name"] == "Cafe Name" for item in result["items"])
