from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app


def test_api_requires_sign_in_by_default():
    response = TestClient(create_app()).get("/api/auth/me")
    assert response.status_code == 401


def test_reviewer_can_open_approval_queue():
    app = create_app()
    with patch("app.main.authenticate_request", new=AsyncMock(return_value={"id": "u1", "email": "reviewer@example.com", "role": "reviewer"})), \
         patch("app.routers.approvals.list_pending_approvals", new=AsyncMock(return_value={"items": [], "total": 0, "has_more": False})):
        response = TestClient(app).get("/api/approvals", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_reviewer_cannot_open_campaign_api():
    app = create_app()
    with patch("app.main.authenticate_request", new=AsyncMock(return_value={"id": "u1", "email": "reviewer@example.com", "role": "reviewer"})):
        response = TestClient(app).get("/api/campaigns", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 403


def test_operator_cannot_approve_emails():
    app = create_app()
    with patch("app.main.authenticate_request", new=AsyncMock(return_value={"id": "u2", "email": "operator@example.com", "role": "operator"})):
        response = TestClient(app).post("/api/emails/00000000-0000-0000-0000-000000000001/approve", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 403
