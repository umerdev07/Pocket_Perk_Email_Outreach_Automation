import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings, get_settings
from app.integrations import claude_client as client


def cfg(key="test-secret", model="claude-sonnet-5-5"):
    return Settings(_env_file=None, claude_api_key=key, claude_model=model)


class Response:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload
        self.is_success = 200 <= status < 300
    def json(self): return self.payload


class FakeClient:
    outcomes, calls = [], []
    def __init__(self, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def post(self, url, *, headers, json):
        self.calls.append((url, headers, json))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception): raise outcome
        return outcome


def reset(*outcomes): FakeClient.outcomes, FakeClient.calls = list(outcomes), []
def ok(content="hello"):
    return Response(200, {"content": [{"type": "text", "text": content}]})


def test_success_and_auth_headers_and_system_mapping():
    reset(ok())
    messages = [{"role": "system", "content": "Be concise."}, {"role": "user", "content": "Hi"}]
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        assert asyncio.run(client.chat_completion(messages, cfg())) == "hello"
    url, headers, body = FakeClient.calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "test-secret"
    assert headers["anthropic-version"] == client.ANTHROPIC_API_VERSION
    assert body["model"] == "claude-sonnet-5-5"
    assert body["system"] == "Be concise."
    assert body["messages"] == [{"role": "user", "content": "Hi"}]


def test_429_retries_twice_with_backoff():
    reset(Response(429, {}), Response(429, {}), ok())
    with patch.object(client.httpx, "AsyncClient", FakeClient), patch.object(client.asyncio, "sleep", new=AsyncMock()) as sleep:
        assert asyncio.run(client.chat_completion([{"role": "user", "content": "Hi"}], cfg())) == "hello"
    assert len(FakeClient.calls) == 3
    assert [c.args[0] for c in sleep.await_args_list] == [0.5, 1.0]


def test_missing_key_error_is_safe():
    with pytest.raises(client.MissingClaudeAPIKeyError) as exc:
        asyncio.run(client.chat_completion([], cfg(key="")))
    assert "test-secret" not in str(exc.value)


def test_missing_model_error():
    with pytest.raises(client.MissingClaudeModelError):
        asyncio.run(client.chat_completion([], cfg(model="")))


def test_timeout_has_custom_safe_error():
    reset(httpx.ReadTimeout("private detail"))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        with pytest.raises(client.ClaudeTimeoutError):
            asyncio.run(client.chat_completion([{"role": "user", "content": "Hi"}], cfg()))


def test_json_fence_parse_and_one_retry():
    reset(ok("not json"), ok('```json\n{"ok":true}\n```'))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        assert asyncio.run(client.chat_completion_json([{"role": "user", "content": "JSON please"}], cfg())) == {"ok": True}
    assert len(FakeClient.calls) == 2


@pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1" or not os.getenv("CLAUDE_API_KEY") or not os.getenv("CLAUDE_MODEL"),
                    reason="Requires RUN_LIVE_TESTS=1 and configured Claude API key/model.")
def test_live_claude():
    config = Settings(_env_file=None, claude_api_key=os.environ["CLAUDE_API_KEY"],
                      claude_model=os.environ["CLAUDE_MODEL"])
    assert asyncio.run(client.chat_completion([{"role": "user", "content": "Reply with OK"}], config))


@pytest.mark.skipif(os.getenv("RUN_CLAUDE_PROMPT_TEST") != "1",
                    reason="Set RUN_CLAUDE_PROMPT_TEST=1 and run this test explicitly to enter a prompt.")
def test_claude_with_your_prompt():
    """Manual live diagnostic. Run only by its node id with pytest -s."""
    config = get_settings()
    if not config.claude_api_key:
        pytest.fail("CLAUDE_API_KEY is missing from backend/.env or the environment.")
    if not config.claude_model:
        pytest.fail("CLAUDE_MODEL is missing from backend/.env or the environment.")
    prompt = input("Enter a test prompt for Claude (no secrets): ").strip()
    if not prompt:
        pytest.fail("Prompt cannot be empty.")
    print(f"\nConfigured model: {config.claude_model}")
    try:
        answer = asyncio.run(client.chat_completion([{"role": "user", "content": prompt}], config))
    except client.ClaudeAPIError as exc:
        pytest.fail(f"Claude API check failed: {exc}")
    print(f"Claude responded successfully:\n{answer}")
