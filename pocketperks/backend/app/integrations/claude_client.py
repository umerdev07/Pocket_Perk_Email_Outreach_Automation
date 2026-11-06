"""Claude Messages API client for drafts, personalization, and risk checks."""
import asyncio
import json
import re
from typing import Any

import httpx

from app.config import Settings, get_settings

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2026-10-21"
MAX_RETRIES = 2


class ClaudeAPIError(Exception):
    """Safe base error; never includes credentials or raw provider response."""


class MissingClaudeAPIKeyError(ClaudeAPIError): pass
class MissingClaudeModelError(ClaudeAPIError): pass
class ClaudeRateLimitedError(ClaudeAPIError): pass
class ClaudeTimeoutError(ClaudeAPIError): pass
class ClaudeAuthenticationError(ClaudeAPIError): pass
class ClaudeBadResponseError(ClaudeAPIError): pass


def _request_payload(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    system_parts: list[str] = []
    api_messages: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str):
            raise ClaudeBadResponseError("Claude messages must contain text content.")
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            api_messages.append({"role": role, "content": content})
        else:
            raise ClaudeBadResponseError("Claude message role is not supported.")
    if not api_messages:
        raise ClaudeBadResponseError("Claude request needs at least one user or assistant message.")

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 2048,
        "messages": api_messages,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    return payload


async def chat_completion(messages: list[dict[str, Any]], settings: Settings | None = None) -> str:
    config = settings or get_settings()
    if not config.claude_api_key:
        raise MissingClaudeAPIKeyError("Claude API key is not configured.")
    if not config.claude_model:
        raise MissingClaudeModelError("Claude model is not configured.")

    headers = {
        "x-api-key": config.claude_api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }
    body = _request_payload(messages, config.claude_model)

    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=60.0) as session:
                response = await session.post(MESSAGES_URL, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ClaudeTimeoutError("Claude API request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ClaudeBadResponseError("Could not communicate with Claude API.") from exc

        if response.status_code == 429:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            raise ClaudeRateLimitedError("Claude API rate limit persisted after retries.")
        if 500 <= response.status_code <= 599:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            raise ClaudeBadResponseError("Claude API service error persisted after retries.")
        if response.status_code in {401, 403}:
            raise ClaudeAuthenticationError("Claude API authentication failed. Check the configured key and access.")
        if not response.is_success:
            raise ClaudeBadResponseError(f"Claude API returned HTTP {response.status_code}.")

        try:
            content_blocks = response.json()["content"]
            result = "".join(
                block["text"] for block in content_blocks
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ClaudeBadResponseError("Claude API returned an invalid Messages response.") from exc
        if not result.strip():
            raise ClaudeBadResponseError("Claude API returned an empty text response.")
        return result.strip()

    raise ClaudeBadResponseError("Claude API request failed.")


def _strip_fence(text: str) -> str:
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text.strip(), re.I | re.S)
    return match.group(1).strip() if match else text.strip()


async def chat_completion_json(messages: list[dict[str, Any]], settings: Settings | None = None) -> Any:
    """Request JSON text and make one safe correction request if parsing fails."""
    base = list(messages)
    request = base + [{"role": "user", "content": "Return only valid JSON, without Markdown fences."}]
    for attempt in range(2):
        text = await chat_completion(request, settings)
        try:
            return json.loads(_strip_fence(text))
        except json.JSONDecodeError as exc:
            if attempt:
                raise ClaudeBadResponseError("Claude did not return valid JSON after one correction request.") from exc
            request = base + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "That was not valid JSON. Return one valid JSON value only."},
            ]
    raise ClaudeBadResponseError("Claude JSON response could not be parsed.")
