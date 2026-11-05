import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings, get_settings
from app.integrations import instantly_client as client


def cfg(key="test-instantly-key"):
    return Settings(_env_file=None, instantly_api_key=key)


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self.payload = payload
        self.is_success = 200 <= status < 300

    def json(self):
        return self.payload


class FakeClient:
    outcomes, calls, posts = [], [], []

    def __init__(self, **kwargs):
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, *, headers, params):
        self.calls.append((url, headers, params))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def post(self, url, *, headers, json):
        self.posts.append((url, headers, json))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def reset(*outcomes):
    FakeClient.outcomes, FakeClient.calls, FakeClient.posts = list(outcomes), [], []


def ok(items=None, cursor=None):
    return Response(200, {"items": items or [], "next_starting_after": cursor})


def test_list_accounts_uses_v2_bearer_auth_and_returns_safe_account_fields():
    reset(ok([{"email": "sender@example.test", "status": 1, "warmup_status": 1,
              "provider_code": 2, "daily_limit": 25, "organization": "private-id",
              "status_message": {"response": "private provider details"}}]))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        result = asyncio.run(client.list_sending_accounts(cfg()))

    assert result == [{"email": "sender@example.test", "status": 1, "warmup_status": 1,
                       "provider_code": 2, "daily_limit": 25}]
    url, headers, params = FakeClient.calls[0]
    assert url == "https://api.instantly.ai/api/v2/accounts"
    assert headers["Authorization"] == "Bearer test-instantly-key"
    assert params == {"limit": 100}


def test_list_accounts_follows_pagination_cursor():
    reset(ok([{"email": "one@example.test", "status": 1}], "2026-11-01T00:00:00Z&one@example.test"),
          ok([{"email": "two@example.test", "status": 2}]))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        result = asyncio.run(client.list_sending_accounts(cfg()))

    assert [row["email"] for row in result] == ["one@example.test", "two@example.test"]
    assert FakeClient.calls[1][2]["starting_after"] == "2026-11-01T00:00:00Z&one@example.test"


def test_rate_limit_retries_twice_with_backoff():
    reset(Response(429, {}), Response(429, {}), ok())
    with patch.object(client.httpx, "AsyncClient", FakeClient), \
         patch.object(client.asyncio, "sleep", new=AsyncMock()) as sleep:
        assert asyncio.run(client.list_sending_accounts(cfg())) == []
    assert len(FakeClient.calls) == 3
    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1.0]


def test_missing_key_error_is_safe():
    with pytest.raises(client.MissingAPIKeyError) as exc:
        asyncio.run(client.list_sending_accounts(cfg(key="")))
    assert "test-instantly-key" not in str(exc.value)


def test_insufficient_scope_error_does_not_leak_provider_payload():
    reset(Response(403, {"message": "private key detail"}))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        with pytest.raises(client.AuthenticationError) as exc:
            asyncio.run(client.list_sending_accounts(cfg()))
    assert "private key detail" not in str(exc.value)
    assert "test-instantly-key" not in str(exc.value)


def test_timeout_uses_safe_custom_error():
    reset(httpx.ReadTimeout("private network detail"))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        with pytest.raises(client.InstantlyTimeoutError) as exc:
            asyncio.run(client.list_sending_accounts(cfg()))
    assert "private network detail" not in str(exc.value)


def test_repeated_pagination_cursor_is_rejected():
    reset(ok([], "same-cursor"), ok([], "same-cursor"))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        with pytest.raises(client.BadResponseError, match="repeated"):
            asyncio.run(client.list_sending_accounts(cfg()))


def test_create_campaign_posts_draft_payload_without_activating_campaign():
    campaign_id = "88a2b7d2-7609-4f82-99d6-fc1d058cd83d"
    payload = {"name": "PocketPerks internal test", "campaign_schedule": {"schedules": []},
               "sequences": [{"steps": []}]}
    reset(Response(201, {"id": campaign_id, "name": payload["name"], "status": "draft"}))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        result = asyncio.run(client.create_campaign(payload, cfg()))

    assert result == {"id": campaign_id, "name": payload["name"], "status": "draft"}
    url, headers, sent_payload = FakeClient.posts[0]
    assert url == "https://api.instantly.ai/api/v2/campaigns"
    assert headers["Authorization"] == "Bearer test-instantly-key"
    assert sent_payload == payload


def test_add_campaign_leads_chunks_at_provider_limit():
    campaign_id = "88a2b7d2-7609-4f82-99d6-fc1d058cd83d"
    leads = [{"email": f"test-{index}@example.test", "custom_variables": {"step_1_subject": "Test"}}
             for index in range(1001)]
    created_first = [{"index": index, "id": f"provider-{index}"} for index in range(1000)]
    reset(Response(200, {"created_leads": created_first}),
          Response(200, {"created_leads": [{"index": 0, "id": "provider-1000"}]}))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        result = asyncio.run(client.add_campaign_leads(campaign_id, leads, cfg()))

    assert result["attempted"] == 1001
    assert result["created"] == 1001
    assert result["batches"] == 2
    assert result["created_leads"][0] == {"index": 0, "id": "provider-0", "email": "test-0@example.test"}
    assert result["created_leads"][-1] == {
        "index": 1000, "id": "provider-1000", "email": "test-1000@example.test"
    }
    assert [len(call[2]["leads"]) for call in FakeClient.posts] == [1000, 1]
    assert all(call[2]["campaign_id"] == campaign_id for call in FakeClient.posts)
    assert all(call[2]["skip_if_in_campaign"] is True for call in FakeClient.posts)
    assert all(call[0] == "https://api.instantly.ai/api/v2/leads/add" for call in FakeClient.posts)


def test_add_campaign_leads_returns_provider_skip_counts_without_claiming_created_ids():
    campaign_id = "88a2b7d2-7609-4f82-99d6-fc1d058cd83d"
    reset(Response(200, {"created_leads": [{"index": 0, "id": "provider-1"}],
                         "skipped_count": 1, "invalid_email_count": 0,
                         "in_blocklist": 0, "duplicated_leads": 0,
                         "duplicate_email_count": 0}))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        result = asyncio.run(client.add_campaign_leads(campaign_id, [
            {"email": "one@example.test"}, {"email": "two@example.test"}
        ], cfg()))
    assert result["created"] == 1
    assert result["skipped"] == 1
    assert result["created_leads"] == [{"index": 0, "id": "provider-1", "email": "one@example.test"}]


def test_post_timeout_is_not_retried_because_write_outcome_is_ambiguous():
    reset(httpx.ReadTimeout("private network detail"))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        with pytest.raises(client.InstantlyTimeoutError, match="check provider state"):
            asyncio.run(client.create_campaign({"name": "Test"}, cfg()))
    assert len(FakeClient.posts) == 1


def test_lead_payload_validation_happens_before_any_provider_call():
    campaign_id = "88a2b7d2-7609-4f82-99d6-fc1d058cd83d"
    reset()
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        with pytest.raises(ValueError, match="non-empty email"):
            asyncio.run(client.add_campaign_leads(campaign_id, [{"email": "  "}], cfg()))
    assert FakeClient.posts == []


@pytest.mark.skipif(os.getenv("RUN_INSTANTLY_READ_TEST") != "1",
                    reason="Set RUN_INSTANTLY_READ_TEST=1 to call the real Instantly accounts:read endpoint.")
def test_live_instantly_accounts_read():
    config = get_settings()
    if not config.instantly_api_key:
        pytest.fail("INSTANTLY_API_KEY is missing from backend/.env or the environment.")
    accounts = asyncio.run(client.list_sending_accounts(config))
    print(f"Instantly read-only check succeeded; connected sending accounts: {len(accounts)}")
