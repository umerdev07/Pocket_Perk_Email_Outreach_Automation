"""PostgreSQL persistence facade for campaign runs."""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.config import Settings


def _campaign_window_estimate(campaign: dict, total_leads: int, sender_accounts: list[dict]) -> dict | None:
    """Conservative preflight from the configured daily window and mailbox limits."""
    try:
        start = datetime.fromisoformat(str(campaign["start_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(campaign["end_at"]).replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            return None
        zone = ZoneInfo(str(campaign.get("timezone") or "UTC"))
        local_start, local_end = start.astimezone(zone), end.astimezone(zone)
        daily_start = str(campaign.get("daily_send_start") or local_start.strftime("%H:%M"))[:5]
        daily_end = str(campaign.get("daily_send_end") or local_end.strftime("%H:%M"))[:5]
        start_hour, start_min = (int(part) for part in daily_start.split(":"))
        end_hour, end_min = (int(part) for part in daily_end.split(":"))
        window_minutes = end_hour * 60 + end_min - (start_hour * 60 + start_min)
        if window_minutes <= 0:
            return None
        allowed_weekdays = {(int(day) - 1) % 7 for day in (campaign.get("sending_days") or [1, 2, 3, 4, 5])}
        steps = sorted(campaign.get("campaign_steps") or [], key=lambda row: row.get("step_number", 0))
        steps = [step for step in steps if step.get("trigger_type", "scheduled") != "after_conversation"]
        if not steps:
            return None
        followup_days = sum(math.ceil(max(0, int(step.get("delay_minutes") if step.get("delay_minutes") is not None
                    else (step.get("delay_days") or 0) * 1440)) / 1440) for step in steps[1:])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    capacity = 0
    for account in sender_accounts:
        try:
            limit, gap = int(float(account.get("daily_limit"))), float(account.get("sending_gap") or 0)
        except (TypeError, ValueError):
            continue
        if limit > 0:
            capacity += max(1, min(limit, math.floor(window_minutes / gap) if gap > 0 else limit))
    if capacity <= 0:
        return None
    windows = max(1, math.ceil(total_leads * len(steps) / capacity))
    required_windows, required_days = windows + 1, windows + 1 + followup_days
    candidate = local_end
    while True:
        days = (candidate.date() - local_start.date()).days + 1
        allowed = sum((local_start.date() + timedelta(days=n)).weekday() in allowed_weekdays for n in range(max(0, days)))
        if days >= required_days and allowed >= required_windows:
            break
        candidate += timedelta(days=1)
        if (candidate.date() - local_start.date()).days > 730:
            return {"required_days": required_days, "available_days": 730, "sending_windows": windows,
                    "recommended_end_at": None, "too_short": True, "too_long": True}
    available_days = (local_end.date() - local_start.date()).days + 1
    available_windows = sum((local_start.date() + timedelta(days=n)).weekday() in allowed_weekdays
                            for n in range(max(0, available_days)))
    return {"required_days": required_days, "available_days": available_days, "sending_windows": windows,
            "recommended_end_at": candidate.isoformat() if candidate > local_end else None,
            "too_short": candidate > local_end}


def _require_pool(pool):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    return pool


async def start_campaign_run(settings: Settings, campaign_id: str, batch_id: str,
                             sender_emails: list[str] | None = None,
                             sender_accounts: list[dict] | None = None, pool=None) -> dict:
    from app.campaign_runs_postgres import start_campaign_run as fn
    return await fn(_require_pool(pool), campaign_id, batch_id, sender_emails, sender_accounts)


async def get_campaign_run(settings: Settings, run_id: str, pool=None) -> dict | None:
    from app.campaign_runs_postgres import get_campaign_run as fn
    return await fn(_require_pool(pool), run_id)


async def get_campaign_run_instantly_status(settings: Settings, run_id: str, pool=None) -> dict:
    from app.campaign_runs_postgres import get_campaign_run_instantly_status as fn
    return await fn(_require_pool(pool), run_id)
