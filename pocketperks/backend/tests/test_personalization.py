import asyncio
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.services.personalization_service import process_campaign_run


def settings():
    return Settings(_env_file=None, claude_api_key="test-key", claude_model="claude-sonnet-5-5")


def context(leads, step_count=2):
    steps = [{"id": f"step-{n}", "step_number": n, "context": f"Context {n}",
              "draft": {"subject": f"Subject {n}", "body": f"Approved body {n}"}}
             for n in range(1, step_count + 1)]
    return {"run": {"id": "run-1"},
            "campaign": {"id": "campaign-1", "instructions": "Use approved draft facts only", "steps": steps},
            "leads": leads}


def test_run_creates_linked_generated_email_per_step():
    leads = [{"id": "lead-1", "business_name": "Cafe Example", "business_category": "Cafe",
              "city": "Leeds", "website": "https://example.test"}]
    saved, progress = [], []
    async def save(_settings, payload):
        saved.append(payload)
        return payload
    async def update(_settings, _run_id, **values):
        progress.append(values)
    with patch("app.services.personalization_service.load_run_context", new=AsyncMock(return_value=context(leads))), \
         patch("app.services.personalization_service.chat_completion_json", new=AsyncMock(side_effect=[
             {"subject": "Personal 1", "body": "Body 1"}, {"subject": "Personal 2", "body": "Body 2"}])) as ai, \
         patch("app.services.personalization_service.assess_personalized_email", new=AsyncMock(return_value={
             "risk_level": "LOW", "risk_reason": "All checks passed."})) as assess, \
         patch("app.services.personalization_service.save_personalized_email", new=AsyncMock(side_effect=save)), \
         patch("app.services.personalization_service.update_run_progress", new=AsyncMock(side_effect=update)):
        asyncio.run(process_campaign_run(settings(), "run-1"))
    assert len(saved) == 2
    assert [(row["run_id"], row["lead_id"], row["campaign_id"], row["step_id"], row["status"], row["risk_level"])
            for row in saved] == [("run-1", "lead-1", "campaign-1", "step-1", "queued", "LOW"),
                                 ("run-1", "lead-1", "campaign-1", "step-2", "queued", "LOW")]
    assert "Cafe Example" in ai.await_args_list[0].args[0][1]["content"]
    assert assess.await_args_list[0].kwargs["campaign"]["instructions"] == "Use approved draft facts only"
    assert assess.await_args_list[0].kwargs["step"]["context"] == "Context 1"
    assert progress[-1]["status"] == "completed"
    assert progress[-1]["processed"] == 1


def test_missing_business_name_is_skipped_with_reason():
    leads = [{"id": "lead-missing", "business_name": "", "business_category": "Cafe"}]
    with patch("app.services.personalization_service.load_run_context", new=AsyncMock(return_value=context(leads))), \
         patch("app.services.personalization_service.chat_completion_json", new=AsyncMock()) as ai, \
         patch("app.services.personalization_service.save_personalized_email", new=AsyncMock()) as save, \
         patch("app.services.personalization_service.update_run_progress", new=AsyncMock()) as update:
        asyncio.run(process_campaign_run(settings(), "run-1"))
    ai.assert_not_awaited()
    save.assert_not_awaited()
    assert update.await_args.kwargs["skipped"] == 1
    assert update.await_args.kwargs["skip_log"] == [{"lead_id": "lead-missing", "reason": "missing_business_name"}]


def test_one_lead_failure_does_not_stop_following_lead():
    leads = [{"id": "lead-bad", "business_name": "Cafe Bad"},
             {"id": "lead-good", "business_name": "Cafe Good"}]
    saved, updates = [], []
    async def save(_settings, payload):
        saved.append(payload)
        return payload
    async def update(_settings, _run_id, **values):
        updates.append(values)
    with patch("app.services.personalization_service.load_run_context", new=AsyncMock(return_value=context(leads, 1))), \
         patch("app.services.personalization_service.chat_completion_json", new=AsyncMock(side_effect=[
             RuntimeError("model error"), {"subject": "Good", "body": "Generated"}])), \
         patch("app.services.personalization_service.assess_personalized_email", new=AsyncMock(return_value={
             "risk_level": "LOW", "risk_reason": "All checks passed."})), \
         patch("app.services.personalization_service.save_personalized_email", new=AsyncMock(side_effect=save)), \
         patch("app.services.personalization_service.update_run_progress", new=AsyncMock(side_effect=update)):
        asyncio.run(process_campaign_run(settings(), "run-1"))
    assert [row["lead_id"] for row in saved] == ["lead-good"]
    assert updates[-1]["failed"] == 1
    assert updates[-1]["processed"] == 1
    assert updates[-1]["status"] == "completed"
    assert updates[-1]["failure_log"] == [{"lead_id": "lead-bad", "business_name": "Cafe Bad",
                                            "step_number": 1,
                                            "reason": "Unexpected RuntimeError while generating or saving this step."}]


def test_large_run_uses_bounded_concurrency_and_chunked_progress():
    leads = [{"id": f"lead-{n}", "business_name": f"Cafe {n}"} for n in range(7)]
    config = Settings(_env_file=None, claude_api_key="test-key", claude_model="claude-sonnet-5-5",
                      personalization_concurrency=2, personalization_batch_size=2)
    active = maximum_active = 0
    progress = []

    async def generate(_messages, _settings):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return {"subject": "Subject", "body": "Body"}

    async def save(_settings, payload):
        return payload

    async def update(_settings, _run_id, **values):
        progress.append(values)

    with patch("app.services.personalization_service.load_run_context", new=AsyncMock(return_value=context(leads, 1))), \
         patch("app.services.personalization_service.chat_completion_json", new=AsyncMock(side_effect=generate)), \
         patch("app.services.personalization_service.assess_personalized_email", new=AsyncMock(return_value={
             "risk_level": "LOW", "risk_reason": "All checks passed."})), \
         patch("app.services.personalization_service.save_personalized_email", new=AsyncMock(side_effect=save)), \
         patch("app.services.personalization_service.update_run_progress", new=AsyncMock(side_effect=update)):
        asyncio.run(process_campaign_run(config, "run-1"))

    assert maximum_active == 2
    assert progress[-1]["processed"] == 7
    assert progress[-1]["status"] == "completed"
    # Four chunks plus the final status update, rather than a DB update per lead.
    assert len(progress) == 5


def test_large_run_loads_leads_as_database_pages():
    data = context([], 1)
    del data["leads"]
    data["run"]["batch_id"] = "batch-1"
    page = [{"id": f"lead-{n}", "business_name": f"Cafe {n}"} for n in range(3)]
    config = Settings(_env_file=None, claude_api_key="test-key", claude_model="claude-sonnet-5-5",
                      personalization_concurrency=2, personalization_batch_size=2)
    with patch("app.services.personalization_service.load_run_context", new=AsyncMock(return_value=data)), \
         patch("app.services.personalization_service.load_lead_page", new=AsyncMock(return_value=page)) as load_page, \
         patch("app.services.personalization_service.chat_completion_json", new=AsyncMock(return_value={
             "subject": "Subject", "body": "Body"})), \
         patch("app.services.personalization_service.assess_personalized_email", new=AsyncMock(return_value={
             "risk_level": "LOW", "risk_reason": "All checks passed."})), \
         patch("app.services.personalization_service.save_personalized_email", new=AsyncMock()), \
         patch("app.services.personalization_service.update_run_progress", new=AsyncMock()) as update:
        asyncio.run(process_campaign_run(config, "run-1"))

    load_page.assert_awaited_once_with(config, "batch-1", offset=0, limit=500)
    assert update.await_args.kwargs["processed"] == 3
    assert update.await_args.kwargs["status"] == "completed"


def test_fatal_run_failure_is_saved_as_safe_diagnostic_and_status_fallback_works():
    updates = []

    async def load_context(_settings, _run_id):
        raise RuntimeError("provider request failed")

    async def update(_settings, _run_id, **values):
        updates.append(values)
        if values.get("error_detail"):
            raise RuntimeError("diagnostic column not installed")

    with patch("app.services.personalization_service.load_run_context", new=AsyncMock(side_effect=load_context)), \
         patch("app.services.personalization_service.update_run_progress", new=AsyncMock(side_effect=update)):
        asyncio.run(process_campaign_run(settings(), "run-1"))

    assert updates[0]["status"] == "failed"
    assert "RuntimeError" in updates[0]["error_detail"]
    assert "provider request failed" not in updates[0]["error_detail"]
    assert updates[1]["status"] == "failed"
    assert "error_detail" not in updates[1]
