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
    outcomes, calls = [], []

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


def reset(*outcomes):
    FakeClient.outcomes, FakeClient.calls = list(outcomes), []


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


@pytest.mark.skipif(os.getenv("RUN_INSTANTLY_READ_TEST") != "1",
                    reason="Set RUN_INSTANTLY_READ_TEST=1 to call the real Instantly accounts:read endpoint.")
def test_live_instantly_accounts_read():
    config = get_settings()
    if not config.instantly_api_key:
        pytest.fail("INSTANTLY_API_KEY is missing from backend/.env or the environment.")
    accounts = asyncio.run(client.list_sending_accounts(config))
    print(f"Instantly read-only check succeeded; connected sending accounts: {len(accounts)}")
