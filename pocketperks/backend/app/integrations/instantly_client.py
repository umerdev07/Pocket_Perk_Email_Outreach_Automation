"""Instantly API v2 client.

Endpoint and bearer authentication follow the official API docs:
https://developer.instantly.ai/api-reference/account/list-account

Campaign creation and lead sync use the API v2 campaign and bulk-add-leads
endpoints. This module deliberately has no campaign activation or send method:
creating a draft and syncing data must not start outreach.
"""
import asyncio
from typing import Any
from uuid import UUID

import httpx

from app.config import Settings, get_settings

ACCOUNTS_URL = "https://api.instantly.ai/api/v2/accounts"
CAMPAIGNS_URL = "https://api.instantly.ai/api/v2/campaigns"
EMAILS_URL = "https://api.instantly.ai/api/v2/emails"
ADD_LEADS_URL = "https://api.instantly.ai/api/v2/leads/add"
PAGE_SIZE = 100
MAX_LEADS_PER_REQUEST = 1000
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


class InstantlyWriteError(InstantlyRequestError):
    """A provider-side write failed; messages contain no raw response data."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 outcome_unknown: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown


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


async def list_received_emails(*, settings: Settings | None = None, limit: int = 100,
                               cursor: str | None = None,
                               min_timestamp_created: str | None = None) -> dict[str, Any]:
    """Read one bounded page of received Instantly Unibox messages only."""
    config = settings or get_settings()
    headers = _write_headers(config)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Email page size must be between 1 and 100.")
    params: dict[str, Any] = {"limit": limit, "email_type": "received", "sort_order": "desc"}
    if cursor:
        params["starting_after"] = cursor
    if min_timestamp_created:
        params["min_timestamp_created"] = min_timestamp_created
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as session:
                response = await session.get(EMAILS_URL, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            raise InstantlyTimeoutError("Instantly reply sync timed out.") from exc
        except httpx.HTTPError as exc:
            raise InstantlyRequestError("Could not communicate with Instantly reply inbox.") from exc
        if response.status_code == 429 or 500 <= response.status_code <= 599:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BASE_SECONDS * (2 ** attempt))
                continue
            if response.status_code == 429:
                raise RateLimitedError("Instantly reply sync rate limit persisted after retries.")
            raise InstantlyRequestError("Instantly reply inbox service error persisted after retries.")
        if response.status_code in (401, 403):
            raise AuthenticationError("Instantly rejected the API key or its emails:read permission.")
        if response.status_code == 402:
            raise PlanAccessError("The Instantly workspace plan does not allow reading inbox emails.")
        if not response.is_success:
            raise InstantlyRequestError(f"Instantly returned HTTP {response.status_code} for inbox replies.")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise BadResponseError("Instantly returned invalid JSON for inbox replies.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise BadResponseError("Instantly returned an unexpected inbox reply list.")
        items = payload["items"]
        if any(not isinstance(item, dict) for item in items):
            raise BadResponseError("Instantly returned an invalid inbox reply entry.")
        next_cursor = payload.get("next_starting_after")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise BadResponseError("Instantly returned an invalid inbox pagination cursor.")
        return {"items": items, "next_starting_after": next_cursor}
    raise InstantlyRequestError("Instantly reply sync failed.")


def _write_headers(settings: Settings) -> dict[str, str]:
    if not settings.instantly_api_key:
        raise MissingAPIKeyError("Instantly API key is not configured.")
    return {
        "Authorization": f"Bearer {settings.instantly_api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _write_error(response: httpx.Response, action: str) -> None:
    if response.status_code in (401, 403):
        raise AuthenticationError(f"Instantly rejected the API key or its permission for {action}.")
    if response.status_code == 402:
        raise PlanAccessError(f"The Instantly workspace plan does not allow {action}.")
    if response.status_code == 429:
        raise RateLimitedError(f"Instantly rate limited {action}; retry the operation later.")
    if response.status_code >= 500:
        raise InstantlyWriteError(
            f"Instantly returned HTTP {response.status_code} while attempting {action}; provider outcome may be unknown.",
            status_code=response.status_code,
            outcome_unknown=True,
        )
    provider_reason = None
    try:
        error_payload = response.json()
    except (ValueError, TypeError):
        error_payload = None
    if isinstance(error_payload, dict):
        for field in ("message", "detail", "error"):
            value = error_payload.get(field)
            if isinstance(value, str) and value.strip():
                provider_reason = " ".join(value.split())[:160]
                break
    reason = f": {provider_reason}" if provider_reason else ""
    raise InstantlyWriteError(
        f"Instantly rejected {action} (HTTP {response.status_code}){reason}.",
        status_code=response.status_code,
    )


def _count_from_response(result: dict[str, Any], field: str) -> int:
    value = result.get(field, 0)
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise BadResponseError("Instantly returned an invalid lead-sync summary.")
    return value


async def _post_write(url: str, payload: dict[str, Any], *, settings: Settings,
                      action: str) -> dict[str, Any]:
    """Make one non-retried POST. Ambiguous timeouts must not duplicate writes."""
    headers = _write_headers(settings)
    try:
        async with httpx.AsyncClient(timeout=30.0) as session:
            response = await session.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise InstantlyTimeoutError(f"Instantly {action} timed out; check provider state before retrying.") from exc
    except httpx.HTTPError as exc:
        raise InstantlyRequestError(f"Could not communicate with Instantly while attempting {action}.") from exc
    if not response.is_success:
        _write_error(response, action)
    try:
        result = response.json()
    except (ValueError, TypeError) as exc:
        raise BadResponseError(f"Instantly returned invalid JSON after {action}.") from exc
    if not isinstance(result, dict):
        raise BadResponseError(f"Instantly returned an unexpected response after {action}.")
    return result


async def create_campaign(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Create an Instantly campaign draft. This does not activate or send it."""
    config = settings or get_settings()
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str) or not payload["name"].strip():
        raise ValueError("Campaign payload requires a non-empty name.")
    result = await _post_write(CAMPAIGNS_URL, payload, settings=config, action="campaign creation")
    campaign_id = result.get("id")
    if not isinstance(campaign_id, str):
        raise BadResponseError("Instantly created a campaign but returned no campaign ID.")
    try:
        UUID(campaign_id)
    except ValueError as exc:
        raise BadResponseError("Instantly returned an invalid campaign ID.") from exc
    return {"id": campaign_id, "name": result.get("name", payload["name"]),
            "status": result.get("status", "draft")}


async def add_campaign_leads(campaign_id: str, leads: list[dict[str, Any]],
                             settings: Settings | None = None) -> dict[str, Any]:
    """Sync leads into a campaign in API-sized chunks; never activates the campaign.

    The caller must supply already-approved/eligible rows and include a valid
    email. The provider validates emails and checks blocklists/existing records
    on this endpoint. Provider response detail is intentionally not logged.
    """
    config = settings or get_settings()
    try:
        UUID(campaign_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("A valid Instantly campaign ID is required.") from exc
    if not isinstance(leads, list):
        raise ValueError("Leads must be provided as a list.")
    if not leads:
        return {"attempted": 0, "batches": 0}
    if any(not isinstance(row, dict) or not isinstance(row.get("email"), str)
           or not row["email"].strip() for row in leads):
        raise ValueError("Every lead must include a non-empty email address.")

    batches = 0
    created_leads: list[dict[str, Any]] = []
    skipped = invalid = blocked = duplicates = 0
    for offset in range(0, len(leads), MAX_LEADS_PER_REQUEST):
        lead_batch = leads[offset:offset + MAX_LEADS_PER_REQUEST]
        payload = {
            "campaign_id": campaign_id,
            "skip_if_in_campaign": True,
            "leads": lead_batch,
        }
        result = await _post_write(ADD_LEADS_URL, payload, settings=config, action="lead sync")
        provider_leads = result.get("created_leads")
        if not isinstance(provider_leads, list):
            raise BadResponseError("Instantly returned no lead mapping after lead sync.")
        seen_indexes: set[int] = set()
        for provider_lead in provider_leads:
            if not isinstance(provider_lead, dict):
                raise BadResponseError("Instantly returned an invalid lead mapping.")
            local_index, provider_id = provider_lead.get("index"), provider_lead.get("id")
            if (not isinstance(local_index, int) or isinstance(local_index, bool)
                    or local_index < 0 or local_index >= len(lead_batch)
                    or not isinstance(provider_id, str) or not provider_id
                    or local_index in seen_indexes):
                raise BadResponseError("Instantly returned an invalid lead mapping.")
            seen_indexes.add(local_index)
            created_leads.append({
                "index": offset + local_index,
                "id": provider_id,
                "email": lead_batch[local_index]["email"].strip().lower(),
            })
        skipped += _count_from_response(result, "skipped_count")
        invalid += _count_from_response(result, "invalid_email_count")
        blocked += _count_from_response(result, "in_blocklist")
        duplicates += (_count_from_response(result, "duplicated_leads")
                       + _count_from_response(result, "duplicate_email_count"))
        batches += 1
    return {"attempted": len(leads), "created": len(created_leads), "skipped": skipped,
            "invalid": invalid, "blocked": blocked, "duplicates": duplicates,
            "batches": batches, "created_leads": created_leads}
