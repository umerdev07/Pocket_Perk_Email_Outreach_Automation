from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
def test_dashboard_detail_uses_postgresql_repository():
    expected = {"run_id": str(uuid4()), "campaign_name": "Test", "status": "completed"}
    pool = object()
    with patch("app.dashboard_postgres.get_dashboard_run_detail", new=AsyncMock(return_value=expected)) as get_detail:
        from app.dashboard_db import get_dashboard_run_detail
        import asyncio
        result = asyncio.run(get_dashboard_run_detail(None, expected["run_id"], pool=pool))
    assert result == expected
    get_detail.assert_awaited_once_with(pool, expected["run_id"])


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
