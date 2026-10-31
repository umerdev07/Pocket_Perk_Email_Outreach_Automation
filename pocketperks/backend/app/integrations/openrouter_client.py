"""OpenRouter Chat Completions client. Endpoint/auth follow official docs:
https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request
"""
import asyncio
import json
import re
from typing import Any

import httpx

from app.config import Settings, get_settings

CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 2


class OpenRouterError(Exception):
    """Safe base error; never includes credentials or raw provider response."""


class MissingAPIKeyError(OpenRouterError): pass
class MissingModelError(OpenRouterError): pass
class RateLimitedError(OpenRouterError): pass
class OpenRouterTimeoutError(OpenRouterError): pass
class BadResponseError(OpenRouterError): pass


async def chat_completion(messages: list[dict[str, Any]], settings: Settings | None = None) -> str:
    config = settings or get_settings()
    if not config.openrouter_api_key:
        raise MissingAPIKeyError("OpenRouter API key is not configured.")
    if not config.openrouter_model:
        raise MissingModelError("OpenRouter model is not configured.")
    headers = {"Authorization": f"Bearer {config.openrouter_api_key}", "Content-Type": "application/json"}
    body = {"model": config.openrouter_model, "messages": messages}
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as session:
                response = await session.post(CHAT_COMPLETIONS_URL, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise OpenRouterTimeoutError("OpenRouter request timed out.") from exc
        except httpx.HTTPError as exc:
            raise BadResponseError("Could not communicate with OpenRouter.") from exc
        if response.status_code == 429:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            raise RateLimitedError("OpenRouter rate limit persisted after retries.")
        if 500 <= response.status_code <= 599:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            raise BadResponseError("OpenRouter service error persisted after retries.")
        if not response.is_success:
            raise BadResponseError(f"OpenRouter returned HTTP {response.status_code}.")
        try:
            result = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise BadResponseError("OpenRouter returned an invalid chat completion response.") from exc
        if not isinstance(result, str) or not result.strip():
            raise BadResponseError("OpenRouter returned an empty text response.")
        return result.strip()
    raise BadResponseError("OpenRouter request failed.")


def _strip_fence(text: str) -> str:
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text.strip(), re.I | re.S)
    return match.group(1).strip() if match else text.strip()


async def chat_completion_json(messages: list[dict[str, Any]], settings: Settings | None = None) -> Any:
    """Request JSON and parse it. Free-model structured output support varies;
    verify model-specific support in OpenRouter docs before adding response_format.
    """
    base = list(messages)
    request = base + [{"role": "user", "content": "Return only valid JSON, without Markdown fences."}]
    for attempt in range(2):
        text = await chat_completion(request, settings)
        try:
            return json.loads(_strip_fence(text))
        except json.JSONDecodeError as exc:
            if attempt:
                raise BadResponseError("OpenRouter did not return valid JSON after one correction request.") from exc
            request = base + [{"role": "assistant", "content": text},
                              {"role": "user", "content": "That was not valid JSON. Return one valid JSON value only."}]
    raise BadResponseError("OpenRouter did not return valid JSON.")
