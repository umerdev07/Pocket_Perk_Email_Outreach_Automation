import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.campaign_runs_db import get_campaign_run, start_campaign_run
from app.config import Settings
from app.main import create_app
from app.personalization_db import get_run_personalized_emails


client = TestClient(create_app(auth_enabled=False))
settings = Settings(_env_file=None)


@pytest.mark.parametrize("call", [
    lambda: start_campaign_run(settings, str(uuid4()), str(uuid4())),
    lambda: get_campaign_run(settings, str(uuid4())),
    lambda: get_run_personalized_emails(settings, str(uuid4()), limit=10, offset=0),
])
def test_database_operations_fail_closed_without_postgres_pool(call):
    with pytest.raises(HTTPException) as raised:
        asyncio.run(call())
    assert raised.value.status_code == 503
    assert "PostgreSQL" in raised.value.detail


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
    assert get_run.await_args.kwargs == {"pool": None}


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
    assert get_emails.await_args.kwargs == {"limit": 10, "offset": 10, "pool": None}


def test_run_emails_endpoint_validates_pagination():
    with patch("app.routers.campaign_runs.get_run_personalized_emails", new=AsyncMock()) as get_emails:
        response = client.get(f"/api/campaign-runs/{uuid4()}/emails?limit=101")
    assert response.status_code == 422
    get_emails.assert_not_awaited()


def test_run_lead_emails_endpoint_returns_selected_lead_details():
    run_id, lead_id = uuid4(), uuid4()
    expected = {"lead": {"id": str(lead_id), "business_name": "Cafe Name"},
                "items": [{"id": "email-1", "step": {"step_number": 1}}]}
    with patch("app.routers.campaign_runs.get_run_lead_personalized_emails",
               new=AsyncMock(return_value=expected)) as get_emails:
        response = client.get(f"/api/campaign-runs/{run_id}/leads/{lead_id}/emails")
    assert response.status_code == 200
    assert response.json() == expected
    get_emails.assert_awaited_once()
    assert get_emails.await_args.kwargs == {"pool": None}
