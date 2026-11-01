import asyncio
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.services.campaign_queue_worker import (
    RETRYABLE_RATE_LIMIT_DETAIL,
    _consume_job,
)


def _job():
    return {"message_id": 42, "read_count": 1, "message": {"run_id": "run-1"}}


def test_permanent_run_failure_is_archived_without_resetting_error_or_progress():
    run = {"status": "failed", "processed": 0, "failed": 1,
           "error_detail": "Background processing stopped (HTTPException)."}
    settings = Settings(_env_file=None)
    with patch("app.services.campaign_queue_worker.process_campaign_run", new=AsyncMock()) as process, \
         patch("app.services.campaign_queue_worker.get_campaign_run", new=AsyncMock(return_value=run)), \
         patch("app.services.campaign_queue_worker.archive_personalization_job", new=AsyncMock(return_value=True)) as archive, \
         patch("app.services.campaign_queue_worker.reset_run_for_retry", new=AsyncMock()) as reset, \
         patch("app.services.campaign_queue_worker.extend_personalization_job", new=AsyncMock()) as extend:
        asyncio.run(_consume_job(settings, _job()))

    process.assert_awaited_once_with(settings, "run-1")
    archive.assert_awaited_once_with(settings, 42)
    reset.assert_not_awaited()
    extend.assert_not_awaited()
    assert run["error_detail"] == "Background processing stopped (HTTPException)."


def test_rate_limited_run_is_reset_and_delayed_for_retry():
    run = {"status": "failed", "error_detail": RETRYABLE_RATE_LIMIT_DETAIL}
    settings = Settings(_env_file=None)
    with patch("app.services.campaign_queue_worker.process_campaign_run", new=AsyncMock()), \
         patch("app.services.campaign_queue_worker.get_campaign_run", new=AsyncMock(return_value=run)), \
         patch("app.services.campaign_queue_worker.reset_run_for_retry", new=AsyncMock()) as reset, \
         patch("app.services.campaign_queue_worker.extend_personalization_job", new=AsyncMock(return_value=True)) as extend, \
         patch("app.services.campaign_queue_worker.archive_personalization_job", new=AsyncMock()) as archive:
        asyncio.run(_consume_job(settings, _job()))

    reset.assert_awaited_once_with(settings, "run-1")
    extend.assert_awaited_once_with(settings, 42, visibility_seconds=300)
    archive.assert_not_awaited()
