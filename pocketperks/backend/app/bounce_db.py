"""Normalize provider bounce notifications and block unsafe future sends."""
from datetime import UTC, datetime
import re

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.suppression_db import normalize_email, save_suppressions


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for bounce tracking.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _text(value: object, limit: int = 500) -> str | None:
    if isinstance(value, dict):
        value = value.get("message") or value.get("reason") or value.get("response") or value.get("code")
    if not isinstance(value, (str, int, float)):
        return None
    result = " ".join(str(value).split())
    return result[:limit] or None


def _bounce_type(payload: dict) -> str:
    raw = " ".join(_text(payload.get(key), 200) or "" for key in (
        "bounce_type", "bounceType", "classification", "bounce_status", "smtp_code", "status_code", "code"
    )).casefold()
    reason = " ".join(_text(payload.get(key), 500) or "" for key in (
        "bounce_reason", "reason", "error", "error_message", "smtp_response", "response"
    )).casefold()
    combined = f"{raw} {reason}"
    if any(token in combined for token in ("hard", "permanent", "invalid recipient", "user unknown", "mailbox not found")):
        return "hard"
    if any(token in combined for token in ("soft", "temporary", "transient", "mailbox full", "try again")):
        return "soft"
    if re.search(r"\b5[.\s-]?\d{1,2}[.\s-]?\d{1,2}\b|\b5\d{2}\b", combined):
        return "hard"
    if re.search(r"\b4[.\s-]?\d{1,2}[.\s-]?\d{1,2}\b|\b4\d{2}\b", combined):
        return "soft"
    return "unknown"


def _event_time(payload: dict) -> str:
    for key in ("timestamp", "event_timestamp", "created_at", "timestamp_created"):
        value = payload.get(key)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return (parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed).astimezone(UTC).isoformat()
            except ValueError:
                continue
    return datetime.now(UTC).isoformat()


def _step_number(payload: dict) -> int | None:
    value = payload.get("step") or payload.get("step_number")
    if isinstance(value, dict):
        value = value.get("number") or value.get("step_number")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 10 else None


async def _update_local_email_steps(client, base: str, headers: dict[str, str], *, run_id: str,
                                   lead_id: str, step_number: int | None, bounce_type: str,
                                   reason: str | None, event_at: str) -> None:
    """Reflect a provider bounce and retire queued follow-ups for this recipient."""
    rows = await _rows(await client.get(f"{base}/personalized_emails", params={
        "select": "id,status,campaign_steps(step_number)", "run_id": f"eq.{run_id}",
        "lead_id": f"eq.{lead_id}", "status": "in.(queued,sent)", "limit": "20",
    }, headers=headers), "Could not load local email steps for the bounce event.")
    if not rows:
        return
    steps: list[tuple[int, dict]] = []
    for row in rows:
        relation = row.get("campaign_steps")
        if isinstance(relation, list):
            relation = relation[0] if relation else {}
        number = relation.get("step_number") if isinstance(relation, dict) else None
        if type(number) is int:
            steps.append((number, row))
    target_step = step_number
    if target_step is None:
        # We cannot safely claim which message bounced without a provider step.
        # Still retire queued follow-ups because the provider stops the sequence.
        future_rows = [row for _, row in steps if row.get("status") == "queued"]
        for row in future_rows:
            response = await client.patch(f"{base}/personalized_emails", params={
                "id": f"eq.{row['id']}", "status": "eq.queued",
            }, headers=_headers_from(headers, "return=minimal"), json={
                "status": "cancelled", "send_error": "Stopped by the sending provider after a bounce.",
            })
            if not response.is_success:
                raise HTTPException(status_code=503, detail="Could not stop follow-up steps after a bounce.")
        return

    target = next((row for number, row in steps if number == target_step), None)
    if target:
        response = await client.patch(f"{base}/personalized_emails", params={
            "id": f"eq.{target['id']}", "status": "in.(queued,sent)",
        }, headers=_headers_from(headers, "return=minimal"), json={
            "status": "bounced", "bounced_at": event_at, "bounce_type": bounce_type,
            "send_error": reason or "The recipient mail server rejected this email.",
        })
        if not response.is_success:
            raise HTTPException(status_code=503, detail="Could not update the bounced email status.")
    for number, row in steps:
        if number <= target_step or row.get("status") != "queued":
            continue
        response = await client.patch(f"{base}/personalized_emails", params={
            "id": f"eq.{row['id']}", "status": "eq.queued",
        }, headers=_headers_from(headers, "return=minimal"), json={
            "status": "cancelled", "send_error": "Stopped by the sending provider after an earlier bounce.",
        })
        if not response.is_success:
            raise HTTPException(status_code=503, detail="Could not stop follow-up steps after a bounce.")


def _headers_from(headers: dict[str, str], prefer: str) -> dict[str, str]:
    return {**headers, "Prefer": prefer}


async def _rows(response: httpx.Response, detail: str) -> list[dict]:
    if not response.is_success:
        raise HTTPException(status_code=503, detail=detail)
    try:
        rows = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=detail) from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise HTTPException(status_code=503, detail=detail)
    return rows


async def save_bounce_event(settings: Settings, event_key: str, payload: dict) -> None:
    """Persist minimal bounce details and conservatively block hard/unknown addresses.

    Soft bounces are tracked but not globally blocked because they can be temporary.
    Instantly itself stops the bounced lead's remaining sequence.
    """
    nested = payload.get("data") or payload.get("payload")
    if isinstance(nested, dict):
        payload = {**payload, **nested}
    raw_email = payload.get("lead_email") or payload.get("email") or payload.get("recipient_email")
    if isinstance(raw_email, dict):
        raw_email = raw_email.get("email")
    email = normalize_email(raw_email)
    provider_campaign_id = _text(payload.get("campaign_id") or payload.get("campaignId"), 200)
    bounce_type = _bounce_type(payload)
    reason = None
    for key in ("bounce_reason", "reason", "error_message", "smtp_response", "response", "error"):
        reason = _text(payload.get(key))
        if reason:
            break
    if not reason:
        reason = "Bounce type could not be determined from the provider event." if bounce_type == "unknown" else None
    email_account = _text(payload.get("email_account") or payload.get("emailAccount"), 320)
    base, headers = _base(settings), _headers(settings)
    campaign_mapping = None
    run_id = campaign_id = lead_id = None

    async with httpx.AsyncClient(timeout=20) as client:
        if provider_campaign_id:
            mappings = await _rows(await client.get(f"{base}/instantly_campaign_mappings", params={
                "select": "id,run_id", "instantly_campaign_id": f"eq.{provider_campaign_id}", "limit": "1",
            }, headers=headers), "Could not match the bounce to its campaign.")
            campaign_mapping = mappings[0] if mappings else None
        if campaign_mapping:
            run_id = campaign_mapping.get("run_id")
            runs = await _rows(await client.get(f"{base}/campaign_runs", params={
                "select": "campaign_id", "id": f"eq.{run_id}", "limit": "1",
            }, headers=headers), "Could not match the bounce to its campaign run.")
            campaign_id = runs[0].get("campaign_id") if runs else None
        lead_rows = []
        if email:
            lead_rows = await _rows(await client.get(f"{base}/leads", params={
                "select": "id", "email": f"eq.{email}", "limit": "1",
            }, headers=headers), "Could not match the bounced address to a lead.")
            lead_id = lead_rows[0].get("id") if lead_rows else None
        matched = False
        if campaign_mapping and lead_id:
            mapped = await _rows(await client.get(f"{base}/instantly_lead_mappings", params={
                "select": "id", "campaign_mapping_id": f"eq.{campaign_mapping['id']}",
                "lead_id": f"eq.{lead_id}", "status": "eq.synced", "limit": "1",
            }, headers=headers), "Could not verify the bounced lead's campaign mapping.")
            matched = bool(mapped)

        event_at = _event_time(payload)
        step_number = _step_number(payload)
        if matched and run_id and lead_id:
            await _update_local_email_steps(client, base, headers, run_id=str(run_id), lead_id=str(lead_id),
                step_number=step_number, bounce_type=bounce_type, reason=reason, event_at=event_at)

        insert = await client.post(f"{base}/instantly_bounce_events", params={"on_conflict": "event_key"},
            headers=_headers(settings, "resolution=ignore-duplicates,return=minimal"), json={
                "event_key": event_key,
                "provider_campaign_id": provider_campaign_id,
                "campaign_mapping_id": campaign_mapping.get("id") if campaign_mapping else None,
                "run_id": run_id,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
                "email": email,
                "bounce_type": bounce_type,
                "reason": reason,
                "step_number": step_number,
                "email_account": email_account,
                "event_at": event_at,
                "match_status": "matched" if matched else "unmatched",
            })
        if not insert.is_success:
            raise HTTPException(status_code=503, detail="Could not save the bounce notification.")

    if email and bounce_type in {"hard", "unknown"}:
        description = "Hard bounce" if bounce_type == "hard" else "Unclassified bounce; verify this address before future emails."
        if reason:
            description += f": {reason}"
        await save_suppressions(settings, [{
            "email": email,
            "source": "bounce_auto",
            "reason": description,
            "lead_id": lead_id,
            "campaign_id": campaign_id,
        }])


async def get_bounce_summary(settings: Settings) -> dict:
    base = _base(settings)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{base}/rpc/dashboard_bounce_summary", headers=_headers(settings), json={})
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load bounce activity.")
    try:
        value = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Could not load bounce activity.") from exc
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    raise HTTPException(status_code=503, detail="Bounce activity response was invalid.")
