from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app


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
