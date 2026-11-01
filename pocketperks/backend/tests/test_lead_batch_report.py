import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.config import Settings
from app.lead_batches_db import get_lead_batch_details
from fastapi.testclient import TestClient

from app.main import create_app


class Response:
    is_success = True
    def __init__(self, payload, headers=None): self.payload, self.headers = payload, headers or {}
    def json(self): return self.payload


class FakeClient:
    responses, requests = [], []
    def __init__(self, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


def test_report_returns_batch_counts_and_selected_number_of_leads():
    batch_id = uuid4()
    FakeClient.responses = [
        Response([{"id": str(batch_id), "file_name": "leads.csv", "total_rows": 11,
                   "imported_count": 10, "duplicate_count": 1, "invalid_count": 0,
                   "rejected_count": 0, "reject_log": [], "created_at": "2026-10-31T00:00:00Z"}]),
        Response([{"business_name": f"Cafe {i}", "business_category": "Cafe", "email": f"c{i}@example.test", "city": "Leeds"}
                  for i in range(5)]),
    ]
    FakeClient.requests = []
    with patch("app.routers.leads.httpx.AsyncClient", FakeClient):
        response = TestClient(create_app(auth_enabled=False)).get(f"/api/lead-batches/{batch_id}/report?limit=5")
    assert response.status_code == 200
    assert response.json()["batch"]["imported_count"] == 10
    assert response.json()["shown"] == 5
    assert len(response.json()["leads"]) == 5
    assert FakeClient.requests[1][1]["params"]["limit"] == "5"


def test_report_limit_only_accepts_five_or_ten():
    response = TestClient(create_app(auth_enabled=False)).get(f"/api/lead-batches/{uuid4()}/report?limit=7")
    assert response.status_code == 422


def test_delete_lead_batch_endpoint_deletes_through_database_module():
    batch_id = uuid4()
    with patch("app.routers.leads.delete_lead_batch", new=AsyncMock()) as delete:
        response = TestClient(create_app(auth_enabled=False)).delete(f"/api/lead-batches/{batch_id}")
    assert response.status_code == 204
    delete.assert_awaited_once()
    assert delete.await_args.args[1] == str(batch_id)


def test_batch_details_endpoint_defaults_to_imported_rows_and_pagination():
    batch_id = uuid4()
    expected = {"batch": {"id": str(batch_id), "file_name": "cafes.xlsx"}, "view": "imported",
                "rows": [{"business_name": "Leeds Cafe"}], "total": 73, "limit": 50, "offset": 0,
                "has_more": True}
    with patch("app.routers.leads.get_lead_batch_details", new=AsyncMock(return_value=expected)) as details:
        response = TestClient(create_app(auth_enabled=False)).get(f"/api/lead-batches/{batch_id}/details")
    assert response.status_code == 200
    assert response.json() == expected
    assert details.await_args.args[1] == str(batch_id)
    assert details.await_args.kwargs == {"view": "imported", "limit": 50, "offset": 0}


def test_batch_details_rejects_invalid_view():
    with patch("app.routers.leads.get_lead_batch_details", new=AsyncMock()) as details:
        response = TestClient(create_app(auth_enabled=False)).get(f"/api/lead-batches/{uuid4()}/details?view=all")
    assert response.status_code == 422
    details.assert_not_awaited()


def test_batch_lead_emails_endpoint_returns_all_campaign_emails_for_lead():
    batch_id, lead_id = uuid4(), uuid4()
    expected = {"lead": {"id": str(lead_id)}, "items": [{"subject": "Hello", "campaign": {"name": "Leeds outreach"}}]}
    with patch("app.routers.leads.get_batch_lead_personalized_emails", new=AsyncMock(return_value=expected)) as emails:
        response = TestClient(create_app(auth_enabled=False)).get(
            f"/api/lead-batches/{batch_id}/leads/{lead_id}/personalized-emails")
    assert response.status_code == 200
    assert response.json() == expected
    emails.assert_awaited_once()
    assert emails.await_args.args[1:] == (str(batch_id), str(lead_id))


def test_batch_details_loads_one_page_of_all_imported_columns():
    batch_id = str(uuid4())
    FakeClient.responses = [
        Response([{"id": batch_id, "file_name": "cafes.xlsx", "total_rows": 62, "imported_count": 60,
                   "duplicate_count": 1, "invalid_count": 1, "rejected_count": 0, "reject_log": []}]),
        Response([{"id": "lead-51", "business_name": "Cafe 51", "email": "cafe@example.test"}],
                 {"content-range": "50-50/60"}),
    ]
    FakeClient.requests = []
    settings = Settings(_env_file=None, supabase_url="https://example.supabase.co", supabase_service_key="test-key")
    with patch("app.lead_batches_db.httpx.AsyncClient", FakeClient):
        result = asyncio.run(get_lead_batch_details(settings, batch_id, view="imported", limit=50, offset=50))
    assert result["batch"]["file_name"] == "cafes.xlsx"
    assert result["total"] == 60
    assert result["rows"][0]["business_name"] == "Cafe 51"
    assert result["has_more"] is True
    assert FakeClient.requests[-1][1]["params"]["offset"] == "50"


def test_batch_details_paginates_reject_log_rows():
    batch_id = str(uuid4())
    rejects = [{"row": row, "status": "duplicate", "data": {"business_name": f"Cafe {row}"}}
               for row in range(2, 8)]
    FakeClient.responses = [Response([{"id": batch_id, "file_name": "cafes.csv", "imported_count": 3,
                                       "reject_log": rejects}])]
    FakeClient.requests = []
    settings = Settings(_env_file=None, supabase_url="https://example.supabase.co", supabase_service_key="test-key")
    with patch("app.lead_batches_db.httpx.AsyncClient", FakeClient):
        result = asyncio.run(get_lead_batch_details(settings, batch_id, view="not_imported", limit=3, offset=3))
    assert result["total"] == 6
    assert [item["row"] for item in result["rows"]] == [5, 6, 7]
    assert result["has_more"] is False
