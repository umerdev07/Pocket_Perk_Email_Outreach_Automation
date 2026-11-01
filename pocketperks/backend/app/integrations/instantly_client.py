"""Read-only Instantly API v2 client for checking connected sending accounts.

Endpoint and bearer authentication follow the official API docs:
https://developer.instantly.ai/api-reference/account/list-account
"""
import asyncio
from typing import Any

import httpx

from app.config import Settings, get_settings

ACCOUNTS_URL = "https://api.instantly.ai/api/v2/accounts"
PAGE_SIZE = 100
MAX_RETRIES = 2
RETRY_BASE_SECONDS = 0.5


class InstantlyError(Exception):
    """Safe base error; messages never include credentials or raw provider payloads."""


class MissingAPIKeyError(InstantlyError):
    pass


class AuthenticationError(InstantlyError):
    pass


class PlanAccessError(InstantlyError):
    pass


class RateLimitedError(InstantlyError):
    pass


class InstantlyTimeoutError(InstantlyError):
    pass


class BadResponseError(InstantlyError):
    pass


class InstantlyRequestError(InstantlyError):
    pass


def _safe_account(account: dict[str, Any]) -> dict[str, Any]:
    """Return only sender account fields needed for a safe connection review."""
    allowed = (
        "email", "status", "warmup_status", "provider_code", "setup_pending",
        "is_managed_account", "daily_limit", "sending_gap",
    )
    return {key: account[key] for key in allowed if key in account}


async def _get_page(session: httpx.AsyncClient, headers: dict[str, str], cursor: str | None) -> dict:
    params: dict[str, Any] = {"limit": PAGE_SIZE}
    if cursor:
        params["starting_after"] = cursor

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await session.get(ACCOUNTS_URL, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            raise InstantlyTimeoutError("Instantly account check timed out.") from exc
        except httpx.HTTPError as exc:
            raise InstantlyRequestError("Could not communicate with Instantly.") from exc

        if response.status_code == 429 or 500 <= response.status_code <= 599:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BASE_SECONDS * (2 ** attempt))
                continue
            if response.status_code == 429:
                raise RateLimitedError("Instantly rate limit persisted after retries.")
            raise InstantlyRequestError("Instantly service error persisted after retries.")
        if response.status_code in (401, 403):
            raise AuthenticationError("Instantly rejected the API key or its accounts:read permission.")
        if response.status_code == 402:
            raise PlanAccessError("The Instantly workspace plan does not allow this API request.")
        if not response.is_success:
            raise InstantlyRequestError(f"Instantly returned HTTP {response.status_code}.")

        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise BadResponseError("Instantly returned invalid JSON for the account list.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise BadResponseError("Instantly returned an unexpected account-list response.")
        if any(not isinstance(item, dict) for item in payload["items"]):
            raise BadResponseError("Instantly returned an invalid account entry.")
        next_cursor = payload.get("next_starting_after")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise BadResponseError("Instantly returned an invalid account pagination cursor.")
        return payload

    raise InstantlyRequestError("Instantly account check failed.")


async def list_sending_accounts(settings: Settings | None = None) -> list[dict[str, Any]]:
    """List connected accounts with accounts:read; this function never sends email."""
    config = settings or get_settings()
    if not config.instantly_api_key:
        raise MissingAPIKeyError("Instantly API key is not configured.")

    headers = {"Authorization": f"Bearer {config.instantly_api_key}", "Accept": "application/json"}
    accounts: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0) as session:
        while True:
            payload = await _get_page(session, headers, cursor)
            accounts.extend(_safe_account(account) for account in payload["items"])
            cursor = payload.get("next_starting_after")
            if not cursor:
                return accounts
            if cursor in seen_cursors:
                raise BadResponseError("Instantly repeated an account pagination cursor.")
            seen_cursors.add(cursor)
