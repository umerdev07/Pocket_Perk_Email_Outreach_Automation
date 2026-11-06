import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.draft_service import generate_campaign_drafts


CAMPAIGN = {"id": "camp-1", "name": "UK cafes", "instructions": "Introduce PocketPerks",
            "status": "draft", "campaign_steps": [
                {"id": "step-1", "step_number": 1, "context": "Introduce the loyalty stamp", "generic_drafts": []},
                {"id": "step-2", "step_number": 2, "context": "Follow up", "generic_drafts": []},
            ]}


def test_generate_creates_a_draft_for_each_step():
    with patch("app.services.draft_service.load_campaign", new=AsyncMock(return_value=CAMPAIGN)), \
         patch("app.services.draft_service.chat_completion_json", new=AsyncMock(side_effect=[
             {"subject": "One", "body": "Hi there, first"}, {"subject": "Two", "body": "Hi there, follow-up"}])) as ai, \
         patch("app.services.draft_service.upsert_step_draft", new=AsyncMock(side_effect=lambda _s, step, sub, body: {"step_id": step, "subject": sub, "body": body})) as save:
        result = asyncio.run(generate_campaign_drafts(Settings(_env_file=None, claude_api_key="test-key",
                                                               claude_model="claude-sonnet-5-5"), "camp-1"))
    assert len(result["generated"]) == 2
    assert result["failed"] == []
    assert save.await_count == 2
    assert "One" in ai.await_args_list[1].args[0][1]["content"]


def test_generate_reports_failed_step_and_continues():
    with patch("app.services.draft_service.load_campaign", new=AsyncMock(return_value=CAMPAIGN)), \
         patch("app.services.draft_service.chat_completion_json", new=AsyncMock(side_effect=[RuntimeError("provider"), {"subject": "Two", "body": "Hi there, follow-up"}])), \
         patch("app.services.draft_service.upsert_step_draft", new=AsyncMock(return_value={"id": "draft-2"})) as save:
        result = asyncio.run(generate_campaign_drafts(Settings(_env_file=None, claude_api_key="test-key",
                                                               claude_model="claude-sonnet-5-5"), "camp-1"))
    assert len(result["failed"]) == 1
    assert result["failed"][0]["step_number"] == 1
    save.assert_awaited_once()


def test_generate_endpoint_queues_background_generation():
    queued = {"campaign_id": "camp-1", "generation_status": "generating", "total_steps": 2}
    with patch("app.routers.drafts.queue_campaign_draft_generation", new=AsyncMock(return_value=queued)) as queue, \
         patch("app.routers.drafts.run_campaign_draft_generation_job", new=AsyncMock()) as job:
        response = TestClient(create_app(auth_enabled=False)).post("/api/campaigns/camp-1/drafts/generate")
    assert response.status_code == 202
    assert response.json() == queued
    queue.assert_awaited_once()
    job.assert_awaited_once()


def test_edit_updates_status_and_demotes_approved_campaign():
    updated = {"id": "draft-1", "status": "edited", "subject": "Changed", "body": "Updated"}
    with patch("app.routers.drafts.update_draft", new=AsyncMock(return_value=updated)) as update:
        response = TestClient(create_app(auth_enabled=False)).patch("/api/drafts/draft-1", json={"subject": "Changed", "body": "Updated"})
    assert response.status_code == 200
    assert response.json()["status"] == "edited"
    update.assert_awaited_once()


def test_approve_sets_approved_status_and_can_approve_campaign():
    approved = {"id": "draft-1", "status": "approved", "approved_at": "2026-01-01T00:00:00Z"}
    with patch("app.routers.drafts.approve_draft", new=AsyncMock(return_value=approved)) as approve:
        response = TestClient(create_app(auth_enabled=False)).post("/api/drafts/draft-1/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    approve.assert_awaited_once()


def test_regenerate_returns_accepted_and_schedules_background_job():
    context = {"id": "draft-1", "generation_status": "idle"}
    with patch("app.routers.drafts.get_draft_context", new=AsyncMock(return_value=context)), \
         patch("app.routers.drafts.set_generation_state", new=AsyncMock(return_value={"generation_status": "generating"})) as set_state, \
         patch("app.routers.drafts.run_regeneration_job", new=AsyncMock()) as job:
        response = TestClient(create_app(auth_enabled=False)).post("/api/drafts/draft-1/regenerate")
    assert response.status_code == 202
    assert response.json() == {"id": "draft-1", "generation_status": "generating"}
    assert set_state.await_args.args[1:] == ("draft-1", "generating")
    job.assert_awaited_once()


def test_regenerate_does_not_queue_duplicate_while_generating():
    context = {"generation_status": "generating", "generation_started_at": datetime.now(UTC).isoformat()}
    with patch("app.routers.drafts.get_draft_context", new=AsyncMock(return_value=context)), \
         patch("app.routers.drafts.set_generation_state", new=AsyncMock()) as set_state, \
         patch("app.routers.drafts.run_regeneration_job", new=AsyncMock()) as job:
        response = TestClient(create_app(auth_enabled=False)).post("/api/drafts/draft-1/regenerate")
    assert response.status_code == 202
    assert response.json()["generation_status"] == "generating"
    set_state.assert_not_awaited()
    job.assert_not_awaited()
