import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings, get_settings
from app.integrations import openrouter_client as client


def cfg(key="test-secret", model="vendor/model:free"):
    return Settings(_env_file=None, openrouter_api_key=key, openrouter_model=model)


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
def ok(content="hello"): return Response(200, {"choices": [{"message": {"content": content}}]})


def test_success_and_auth_header():
    reset(ok())
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        assert asyncio.run(client.chat_completion([{"role":"user","content":"Hi"}], cfg())) == "hello"
    url, headers, body = FakeClient.calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-secret"
    assert body["model"] == "vendor/model:free"


def test_429_retries_twice_with_backoff():
    reset(Response(429, {}), Response(429, {}), ok())
    with patch.object(client.httpx, "AsyncClient", FakeClient), patch.object(client.asyncio, "sleep", new=AsyncMock()) as sleep:
        assert asyncio.run(client.chat_completion([], cfg())) == "hello"
    assert len(FakeClient.calls) == 3
    assert [c.args[0] for c in sleep.await_args_list] == [0.5, 1.0]


def test_missing_key_error_is_safe():
    with pytest.raises(client.MissingAPIKeyError) as exc:
        asyncio.run(client.chat_completion([], cfg(key="")))
    assert "test-secret" not in str(exc.value)


def test_missing_model_error():
    with pytest.raises(client.MissingModelError): asyncio.run(client.chat_completion([], cfg(model="")))


def test_timeout_has_custom_safe_error():
    reset(httpx.ReadTimeout("private detail"))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        with pytest.raises(client.OpenRouterTimeoutError): asyncio.run(client.chat_completion([], cfg()))


def test_json_fence_parse_and_one_retry():
    reset(ok("not json"), ok('```json\n{"ok":true}\n```'))
    with patch.object(client.httpx, "AsyncClient", FakeClient):
        assert asyncio.run(client.chat_completion_json([], cfg())) == {"ok": True}
    assert len(FakeClient.calls) == 2


@pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1" or not os.getenv("OPENROUTER_API_KEY"),
                    reason="Requires RUN_LIVE_TESTS=1 and OPENROUTER_API_KEY.")
def test_live_openrouter():
    config = Settings(_env_file=None, openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
                      openrouter_model=os.getenv("OPENROUTER_MODEL", ""))
    assert asyncio.run(client.chat_completion([{"role":"user","content":"Reply with OK"}], config))


@pytest.mark.skipif(os.getenv("RUN_OPENROUTER_PROMPT_TEST") != "1",
                    reason="Set RUN_OPENROUTER_PROMPT_TEST=1 and run this test explicitly to enter a prompt.")
def test_openrouter_with_your_prompt():
    """Manual live diagnostic. Run only by its node id with pytest -s."""
    config = get_settings()
    if not config.openrouter_api_key:
        pytest.fail("OPENROUTER_API_KEY is missing from backend/.env or the environment.")
    if not config.openrouter_model:
        pytest.fail("OPENROUTER_MODEL is missing from backend/.env or the environment.")
    prompt = input("Enter a test prompt for OpenRouter (no secrets): ").strip()
    if not prompt:
        pytest.fail("Prompt cannot be empty.")
    print(f"\nConfigured model: {config.openrouter_model}")
    try:
        answer = asyncio.run(client.chat_completion([{"role": "user", "content": prompt}], config))
    except client.OpenRouterError as exc:
        pytest.fail(f"OpenRouter check failed: {exc}")
    print(f"OpenRouter responded successfully:\n{answer}")
