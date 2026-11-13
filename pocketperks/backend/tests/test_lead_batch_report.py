import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import leads_postgres
from app.main import create_app


class FakePool:
    async def close(self):
        pass


def client():
    app = create_app(auth_enabled=False, queue_worker_enabled=False)
    app.state.postgres_pool = FakePool()
    return TestClient(app)


def test_report_returns_batch_counts_and_selected_number_of_leads():
    batch_id = uuid4()
    expected = {"batch": {"id": str(batch_id), "imported_count": 10},
                "leads": [{"business_name": f"Cafe {i}"} for i in range(5)], "shown": 5}
    with patch("app.routers.leads.leads_postgres.batch_report", new=AsyncMock(return_value=expected)) as report:
        response = client().get(f"/api/lead-batches/{batch_id}/report?limit=5")
    assert response.status_code == 200
    assert response.json() == expected
    report.assert_awaited_once()
    assert report.await_args.args[1:] == (str(batch_id), 5)


def test_report_limit_only_accepts_five_or_ten():
    response = client().get(f"/api/lead-batches/{uuid4()}/report?limit=7")
    assert response.status_code == 422


def test_delete_lead_batch_endpoint_deletes_through_postgres_repository():
    batch_id = uuid4()
    with patch("app.routers.leads.leads_postgres.delete_batch", new=AsyncMock()) as delete:
        response = client().delete(f"/api/lead-batches/{batch_id}")
    assert response.status_code == 204
    delete.assert_awaited_once()
    assert delete.await_args.args[1] == str(batch_id)


def test_batch_details_endpoint_defaults_to_imported_rows_and_pagination():
    batch_id = uuid4()
    expected = {"batch": {"id": str(batch_id), "file_name": "cafes.xlsx"}, "view": "imported",
                "rows": [{"business_name": "Leeds Cafe"}], "total": 73, "limit": 50, "offset": 0,
                "has_more": True}
    with patch("app.routers.leads.leads_postgres.batch_details", new=AsyncMock(return_value=expected)) as details:
        response = client().get(f"/api/lead-batches/{batch_id}/details")
    assert response.status_code == 200
    assert response.json() == expected
    details.assert_awaited_once()
    assert details.await_args.args[1] == str(batch_id)
    assert details.await_args.kwargs == {"view": "imported", "limit": 50, "offset": 0}


def test_batch_details_rejects_invalid_view():
    with patch("app.routers.leads.leads_postgres.batch_details", new=AsyncMock()) as details:
        response = client().get(f"/api/lead-batches/{uuid4()}/details?view=all")
    assert response.status_code == 422
    details.assert_not_awaited()


def test_batch_lead_emails_endpoint_returns_all_campaign_emails_for_lead():
    batch_id, lead_id = uuid4(), uuid4()
    expected = {"lead": {"id": str(lead_id)}, "items": [{"subject": "Hello", "campaign": {"name": "Leeds outreach"}}]}
    with patch("app.routers.leads.leads_postgres.personalized_emails_for_batch_lead",
               new=AsyncMock(return_value=expected)) as emails:
        response = client().get(f"/api/lead-batches/{batch_id}/leads/{lead_id}/personalized-emails")
    assert response.status_code == 200
    assert response.json() == expected
    emails.assert_awaited_once()
    assert emails.await_args.args[1:] == (str(batch_id), str(lead_id))


def test_batch_details_loads_a_postgres_page_of_imported_rows():
    batch_id = str(uuid4())

    class Pool:
        async def fetchrow(self, *_args):
            return {"id": batch_id, "file_name": "cafes.xlsx", "reject_log": []}
        async def fetchval(self, *_args):
            return 60
        async def fetch(self, *_args):
            return [{"id": "lead-51", "business_name": "Cafe 51", "email": "cafe@example.test"}]

    result = asyncio.run(leads_postgres.batch_details(Pool(), batch_id, view="imported", limit=50, offset=50))
    assert result["batch"]["file_name"] == "cafes.xlsx"
    assert result["total"] == 60
    assert result["rows"][0]["business_name"] == "Cafe 51"
    assert result["has_more"] is True


def test_batch_details_paginates_reject_log_rows():
    batch_id = str(uuid4())
    rejects = [{"row": row, "status": "duplicate", "data": {"business_name": f"Cafe {row}"}}
               for row in range(2, 8)]

    class Pool:
        async def fetchrow(self, *_args):
            return {"id": batch_id, "file_name": "cafes.csv", "imported_count": 3, "reject_log": rejects}

    result = asyncio.run(leads_postgres.batch_details(Pool(), batch_id, view="not_imported", limit=3, offset=3))
    assert result["total"] == 6
    assert [item["row"] for item in result["rows"]] == [5, 6, 7]
    assert result["has_more"] is False
