from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app(auth_enabled=False))


def review_context(status="pending_review"):
    return {"email": {"id": "email-1", "subject": "Hello", "body": "Original body", "status": status},
            "lead": {"business_name": "Cafe Example", "email": "hello@example.test", "city": "Leeds"},
            "draft": {"subject": "Hello", "body": "Approved generic draft"},
            "campaign": {"instructions": "Keep it friendly"}, "step": {"context": "Ask for a short conversation"}}


def test_approval_queue_lists_pending_review_emails():
    expected = {"items": [{"id": "email-1", "risk_level": "HIGH"}],
                "total": 1, "limit": 20, "offset": 0, "has_more": False}
    with patch("app.routers.approvals.list_pending_approvals", new=AsyncMock(return_value=expected)) as listing:
        response = client.get("/api/approvals?limit=20")
    assert response.status_code == 200
    assert response.json() == expected
    assert listing.await_args.kwargs == {"limit": 20, "offset": 0, "pool": None}


def test_approval_history_returns_saved_automated_and_human_review_events():
    expected = {"items": [{"id": "history-1", "action": "risk_assessed", "status": "queued",
                           "risk_level": "LOW", "risk_reason": "No obvious content-level spam signals."}],
                "total": 1, "limit": 10, "offset": 5, "has_more": False}
    with patch("app.routers.approvals.list_review_history", new=AsyncMock(return_value=expected)) as listing:
        response = client.get("/api/approvals/history?limit=10&offset=5&search=cafe&days=90")
    assert response.status_code == 200
    assert response.json() == expected
    assert listing.await_args.kwargs == {"limit": 10, "offset": 5, "search": "cafe", "days": 90, "pool": None}


def test_approval_history_rejects_unknown_filter_values():
    response = client.get("/api/approvals/history?days=900")
    assert response.status_code == 422


def test_clear_approval_history_requires_confirmation():
    with patch("app.routers.approvals.clear_review_history", new=AsyncMock()) as clear:
        response = client.post("/api/approvals/history/clear", json={"confirm": False})
    assert response.status_code == 422
    clear.assert_not_awaited()


def test_clear_approval_history_clears_only_the_history_log():
    with patch("app.routers.approvals.clear_review_history", new=AsyncMock(return_value={"cleared": True})) as clear:
        response = client.post("/api/approvals/history/clear", json={"confirm": True})
    assert response.status_code == 200
    assert response.json() == {"cleared": True}
    clear.assert_awaited_once()


def test_edit_rechecks_risk_and_requires_human_approval_again():
    email_id = uuid4()
    updated = {"id": str(email_id), "subject": "Updated", "body": "Original body",
               "status": "pending_review", "risk_level": "MEDIUM"}
    with patch("app.routers.approvals.get_email_review_context",
               new=AsyncMock(return_value=review_context("approved"))), \
         patch("app.routers.approvals.assess_personalized_email", new=AsyncMock(return_value={
             "risk_level": "MEDIUM", "risk_reason": "AI review flagged a claim."})) as assess, \
         patch("app.routers.approvals.update_review_email", new=AsyncMock(return_value=updated)) as update:
        response = client.patch(f"/api/emails/{email_id}", json={"subject": "Updated"})
    assert response.status_code == 200
    assert response.json()["status"] == "pending_review"
    assert assess.await_args.args[:2] == ("Updated", "Original body")
    assert assess.await_args.kwargs["campaign"]["instructions"] == "Keep it friendly"
    assert assess.await_args.kwargs["step"]["context"] == "Ask for a short conversation"
    assert update.await_args.args[3]["risk_reason"].startswith("Email was edited. Content-level spam check:")
    assert update.await_args.args[2] == "approved"
    assert update.await_args.args[3]["approved_version_hash"] is None


def test_low_risk_edit_is_automatically_queued():
    email_id = uuid4()
    with patch("app.routers.approvals.get_email_review_context",
               new=AsyncMock(return_value=review_context("approved"))), \
         patch("app.routers.approvals.assess_personalized_email", new=AsyncMock(return_value={
             "risk_level": "LOW", "risk_reason": "No obvious content-level spam signals."})), \
         patch("app.routers.approvals.update_review_email", new=AsyncMock(return_value={
             "id": str(email_id), "status": "queued", "risk_level": "LOW"})) as update:
        response = client.patch(f"/api/emails/{email_id}", json={"subject": "A clearer subject"})
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert update.await_args.args[3]["status"] == "queued"
    assert update.await_args.args[3]["reviewed_at"] is None


def test_edit_requires_at_least_one_non_empty_field():
    email_id = uuid4()
    with patch("app.routers.approvals.get_email_review_context", new=AsyncMock()) as load:
        response = client.patch(f"/api/emails/{email_id}", json={})
    assert response.status_code == 422
    load.assert_not_awaited()


def test_approve_queues_email_without_marking_it_sent():
    email_id = uuid4()
    updated = {"id": str(email_id), "status": "queued"}
    with patch("app.routers.approvals.get_email_review_context",
               new=AsyncMock(return_value=review_context())), \
         patch("app.routers.approvals.update_review_email", new=AsyncMock(return_value=updated)) as update:
        response = client.post(f"/api/emails/{email_id}/approve")
    assert response.status_code == 200
    values = update.await_args.args[3]
    assert values["status"] == "queued"
    assert values["status"] != "sent"
    assert len(values["approved_version_hash"]) == 64
    assert values["reviewed_at"]


def test_reject_marks_email_rejected():
    email_id = uuid4()
    with patch("app.routers.approvals.get_email_review_context",
               new=AsyncMock(return_value=review_context())), \
         patch("app.routers.approvals.update_review_email", new=AsyncMock(return_value={
             "id": str(email_id), "status": "rejected"})) as update:
        response = client.post(f"/api/emails/{email_id}/reject")
    assert response.status_code == 200
    assert update.await_args.args[3]["status"] == "rejected"


def test_recheck_low_risk_email_auto_queues_it_without_marking_sent():
    email_id = uuid4()
    updated = {"id": str(email_id), "status": "queued", "risk_level": "LOW"}
    with patch("app.routers.approvals.get_email_review_context",
               new=AsyncMock(return_value=review_context())), \
         patch("app.routers.approvals.assess_personalized_email", new=AsyncMock(return_value={
             "risk_level": "LOW", "risk_reason": "No material concern found."})), \
         patch("app.routers.approvals.update_review_email", new=AsyncMock(return_value=updated)) as update:
        response = client.post(f"/api/emails/{email_id}/recheck")
    assert response.status_code == 200
    assert response.json()["auto_queued"] is True
    assert response.json()["sent"] is False
    assert update.await_args.args[2] == "pending_review"
    assert update.await_args.args[3]["status"] == "queued"


def test_recheck_medium_risk_email_keeps_it_in_review():
    email_id = uuid4()
    with patch("app.routers.approvals.get_email_review_context",
               new=AsyncMock(return_value=review_context())), \
         patch("app.routers.approvals.assess_personalized_email", new=AsyncMock(return_value={
             "risk_level": "MEDIUM", "risk_reason": "Concrete concern requires review."})), \
         patch("app.routers.approvals.update_review_email", new=AsyncMock(return_value={
             "id": str(email_id), "status": "pending_review", "risk_level": "MEDIUM"})) as update:
        response = client.post(f"/api/emails/{email_id}/recheck")
    assert response.status_code == 200
    assert response.json()["auto_queued"] is False
    assert response.json()["sent"] is False
    assert update.await_args.args[3]["status"] == "pending_review"
