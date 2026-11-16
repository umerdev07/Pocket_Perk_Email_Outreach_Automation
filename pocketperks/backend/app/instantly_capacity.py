"""Shared sending capacity derived from the connected Instantly inboxes."""
from fastapi import HTTPException

from app.config import Settings

def _is_ready(account: dict) -> bool:
    return str(account.get("status", "")).strip().lower() in {"1", "active", "connected"} \
        and not account.get("setup_pending")


def get_workspace_capacity(settings: Settings, accounts: list[dict]) -> dict:
    """Sum daily limits for eligible active inboxes, with an optional safety ceiling.

    OUTREACH_DAILY_EMAIL_CAPACITY=0 means use Instantly's current total. A
    positive value caps that total; it can never increase provider capacity.
    """
    eligible = [account for account in accounts if _is_ready(account)]
    limits: list[tuple[str, int]] = []
    missing: list[str] = []
    for account in eligible:
        email = str(account.get("email", "")).strip().lower() or "unknown inbox"
        try:
            raw_limit = account.get("daily_limit")
            limit = int(raw_limit)
            if isinstance(raw_limit, bool) or limit <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            missing.append(email)
            continue
        limits.append((email, limit))

    if missing:
        raise HTTPException(
            status_code=503,
            detail="Instantly did not provide a daily limit for every active sending inbox. Check inbox settings, then refresh.",
        )
    provider_total = sum(limit for _, limit in limits)
    if provider_total <= 0:
        raise HTTPException(
            status_code=503,
            detail="No active Instantly inboxes with a daily sending limit are available.",
        )

    safety_ceiling = int(settings.outreach_daily_email_capacity or 0)
    effective = min(provider_total, safety_ceiling) if safety_ceiling > 0 else provider_total
    return {
        "daily_email_capacity": effective,
        "provider_daily_email_capacity": provider_total,
        "capacity_source": "instantly_inboxes" if not safety_ceiling else "instantly_inboxes_with_safety_ceiling",
        "safety_ceiling": safety_ceiling or None,
        "eligible_inbox_count": len(limits),
        "inbox_daily_limits": [{"email": email, "daily_limit": limit} for email, limit in limits],
    }


async def fetch_workspace_capacity(settings: Settings) -> dict:
    """Fetch current account settings from Instantly and derive the workspace cap."""
    from app.integrations.instantly_client import InstantlyError, list_sending_accounts

    try:
        accounts = await list_sending_accounts(settings)
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=f"Could not refresh Instantly inbox limits: {exc}") from exc
    return get_workspace_capacity(settings, accounts)
