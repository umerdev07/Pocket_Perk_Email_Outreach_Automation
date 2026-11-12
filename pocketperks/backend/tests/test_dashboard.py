from unittest.mock import AsyncMock, patch
from uuid import uuid4
import asyncio

from fastapi.testclient import TestClient

from app.main import create_app
from app.dashboard_db import _campaign_run_detail


class DashboardResponse:
    is_success = True

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class DashboardClient:
    async def post(self, url, *, json, headers):
        assert url.endswith("/rpc/dashboard_run_email_totals")
        return DashboardResponse({"queued": 0, "not_added": 8, "sent": 0, "failed": 0,
                                  "pending_review": 0, "bounced": 0, "cancelled": 0})

    async def get(self, url, *, params, headers):
        assert url.endswith("/instantly_campaign_mappings")
        return DashboardResponse([{"status": "failed", "error_detail": "No eligible leads synced to Instantly."}])


def test_campaign_run_detail_does_not_call_unsynced_queued_emails_waiting_to_send():
    run = {"id": str(uuid4()), "campaign_id": str(uuid4()), "batch_id": str(uuid4()),
           "status": "completed", "total_leads": 4, "processed": 4, "skipped": 0,
           "failed": 0, "created_at": "2026-11-12T11:50:00Z", "finished_at": "2026-11-12T11:51:00Z"}
    detail = asyncio.run(_campaign_run_detail(DashboardClient(), "https://db.example.test/rest/v1", {}, run,
                                              "PocketPerks Local Business Follow-up"))

    assert detail["status"] == "completed"  # Personalization completed.
    assert detail["sync_status"] == "failed"  # Provider sync did not.
    assert detail["queued_emails"] == 0
    assert detail["not_added_emails"] == 8


def test_dashboard_summary_returns_database_totals():
    expected = {"total_leads": 120, "total_campaigns": 4, "approved_campaigns": 2,
                "pending_approvals": 8, "queued_not_sent": 15, "active_runs": 1,
                "total_replies": 3, "unread_replies": 1, "instantly_campaigns": 2,
                "instantly_leads_synced": 16,
                "activity": {"days": [], "campaign_status": [], "email_status": []}}
    with patch("app.routers.dashboard.get_dashboard_summary", new=AsyncMock(return_value=expected)) as summary:
        response = TestClient(create_app(auth_enabled=False)).get("/api/dashboard/summary")
    assert response.status_code == 200
    assert response.json() == expected
    summary.assert_awaited_once()


def test_dashboard_can_load_selected_recent_run():
    run_id = uuid4()
    expected = {"run_id": str(run_id), "campaign_name": "Test campaign", "status": "completed",
                "total_leads": 3, "processed": 3, "queued_emails": 1, "sent_emails": 2}
    with patch("app.routers.dashboard.get_dashboard_run_detail", new=AsyncMock(return_value=expected)) as detail:
        response = TestClient(create_app(auth_enabled=False)).get(f"/api/dashboard/runs/{run_id}")
    assert response.status_code == 200
    assert response.json() == expected
    detail.assert_awaited_once()
