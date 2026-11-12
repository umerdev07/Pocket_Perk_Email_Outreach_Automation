import asyncio
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app import replies_db


class Response:
    status_code = 200
    is_success = True

    def __init__(self, rows):
        self.rows = rows

    def json(self):
        return self.rows


class FakeSupabase:
    replies = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, *, params, headers):
        assert url.endswith("/replies")
        return Response(self.replies)


def cfg():
    return Settings(_env_file=None, supabase_url="https://db.example.test",
                    supabase_service_key="service-test")


def test_reply_refresh_does_not_reactivate_historical_opt_out_after_release():
    FakeSupabase.replies = [{
        "id": "reply-1", "lead_id": "lead-1", "campaign_id": "campaign-1",
        "from_email": "owner@example.co.uk", "body": "No thanks",
        "leads": {"email": "owner@example.co.uk", "business_name": "Example Cafe"},
    }]
    with patch("app.replies_db.httpx.AsyncClient", FakeSupabase), \
         patch("app.replies_db.get_recorded_suppression_emails",
               new=AsyncMock(return_value={"owner@example.co.uk"})), \
         patch("app.replies_db.save_suppressions", new=AsyncMock()) as save, \
         patch("app.replies_db.get_suppressed_emails", new=AsyncMock(return_value=set())):
        rows = asyncio.run(replies_db.list_replies(cfg()))

    save.assert_awaited_once_with(cfg(), [])
    assert rows[0]["opted_out"] is False


def test_reply_refresh_backfills_historical_opt_out_without_suppression_record():
    FakeSupabase.replies = [{
        "id": "reply-2", "lead_id": "lead-2", "campaign_id": "campaign-2",
        "from_email": "another@example.co.uk", "body": "Please unsubscribe me",
        "leads": {"email": "another@example.co.uk", "business_name": "Another Cafe"},
    }]
    with patch("app.replies_db.httpx.AsyncClient", FakeSupabase), \
         patch("app.replies_db.get_recorded_suppression_emails", new=AsyncMock(return_value=set())), \
         patch("app.replies_db.save_suppressions", new=AsyncMock()) as save, \
         patch("app.replies_db.get_suppressed_emails", new=AsyncMock(return_value={"another@example.co.uk"})):
        rows = asyncio.run(replies_db.list_replies(cfg()))

    save.assert_awaited_once_with(cfg(), [{
        "email": "another@example.co.uk", "source": "reply_auto",
        "reason": "Recipient explicitly requested no further email.",
        "lead_id": "lead-2", "campaign_id": "campaign-2",
    }])
    assert rows[0]["opted_out"] is True
