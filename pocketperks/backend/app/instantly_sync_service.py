"""Build safe Instantly draft and lead payloads from reviewed PocketPerks emails.

Campaigns produced here are provider-side drafts only. This module has no
activation or sending path.
"""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo
from typing import Any

from app.config import Settings
from app.instantly_sync_db import (create_campaign_mapping, get_lead_mapping_statuses,
                                   load_sync_run_context, load_sync_lead_page,
                                   save_lead_mappings, update_campaign_mapping)
from app.integrations.instantly_client import (BadResponseError, InstantlyTimeoutError,
                                               InstantlyWriteError, add_campaign_leads,
                                               create_campaign)


def build_campaign_payload(campaign: dict[str, Any], steps: list[dict[str, Any]], *,
                           delay_hours: int = 4) -> dict[str, Any]:
    """Build one inactive Instantly sequence using per-lead merge variables."""
    if not isinstance(campaign, dict) or not str(campaign.get("name", "")).strip():
        raise ValueError("Campaign name is required for Instantly sync.")
    start_at, end_at = campaign.get("start_at"), campaign.get("end_at")
    start_date, end_date = campaign.get("start_date"), campaign.get("end_date")
    if start_at and end_at:
        try:
            start_dt = datetime.fromisoformat(str(start_at).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
            if start_dt.tzinfo is None or end_dt.tzinfo is None:
                raise ValueError
            london = ZoneInfo("Europe/London")
            start, end = start_dt.astimezone(london).date(), end_dt.astimezone(london).date()
        except (ValueError, TypeError) as exc:
            raise ValueError("Campaign start and end timestamps must be timezone-aware ISO values.") from exc
    elif start_date and end_date:
        # Compatibility for old campaign records that predate timestamp fields.
        try:
            start, end = date.fromisoformat(str(start_date)), date.fromisoformat(str(end_date))
        except ValueError as exc:
            raise ValueError("Campaign dates must use YYYY-MM-DD format.") from exc
    else:
        raise ValueError("Campaign start and end dates are required for Instantly sync.")
    if end < start:
        raise ValueError("Campaign end date cannot be before its start date.")
    if type(delay_hours) is not int or not 1 <= delay_hours <= 72:
        raise ValueError("Follow-up delay must be between 1 and 72 hours.")
    if not isinstance(steps, list) or not steps:
        raise ValueError("At least one campaign step is required.")

    ordered = sorted(steps, key=lambda item: item.get("step_number", 0))
    sequence_steps = []
    for index, step in enumerate(ordered, start=1):
        if step.get("step_number") != index:
            raise ValueError("Campaign steps must be numbered consecutively from 1.")
        draft = step.get("draft")
        draft_approved = isinstance(draft, dict) and draft.get("status") == "approved"
        if step.get("status") != "approved" and step.get("draft_status") != "approved" and not draft_approved:
            raise ValueError("Every campaign step must have an approved generic draft.")
        sequence_steps.append({
            "type": "email",
            "delay": 0 if index == 1 else delay_hours,
            "delay_unit": "hours",
            "variants": [{
                "subject": "{{pp_email_" + str(index) + "_subject}}",
                "body": "{{pp_email_" + str(index) + "_body}}",
            }],
        })

    return {
        "name": str(campaign["name"]).strip(),
        "is_evergreen": False,
        "campaign_schedule": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "schedules": [{
                "name": "PocketPerks UK weekdays",
                "timing": {"from": "09:00", "to": "17:00"},
                "days": {"0": False, "1": True, "2": True, "3": True,
                         "4": True, "5": True, "6": False},
                "timezone": "Europe/London",
            }],
        },
        "sequences": [{"steps": sequence_steps}],
        "stop_on_reply": True,
    }


def build_lead_payload(lead: dict[str, Any], personalized_steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Map a fully reviewed lead and its step copy to Instantly custom variables."""
    email = lead.get("email") if isinstance(lead, dict) else None
    if not isinstance(email, str) or not email.strip():
        raise ValueError("Lead needs an email address before Instantly sync.")
    if not isinstance(personalized_steps, list) or not personalized_steps:
        raise ValueError("Lead needs all personalized campaign steps before Instantly sync.")

    ordered = sorted(personalized_steps, key=lambda item: item.get("step_number", 0))
    variables: dict[str, str] = {}
    for index, step in enumerate(ordered, start=1):
        if step.get("step_number") != index or step.get("status") != "queued":
            raise ValueError("Only a complete sequence of queued emails can be synced.")
        subject, body = step.get("subject"), step.get("body")
        if not isinstance(subject, str) or not subject.strip() or not isinstance(body, str) or not body.strip():
            raise ValueError("Every queued email needs a subject and body.")
        variables[f"pp_email_{index}_subject"] = subject.strip()
        variables[f"pp_email_{index}_body"] = body.strip()

    business_name = lead.get("business_name") or ""
    return {
        "email": email.strip().lower(),
        "company_name": str(business_name),
        "first_name": str(business_name),
        "website": str(lead.get("website") or ""),
        "phone": str(lead.get("phone") or ""),
        "custom_variables": variables,
    }


async def sync_completed_run(settings: Settings, run_id: str) -> dict[str, int | str]:
    """Create an inactive provider draft and sync only complete queued sequences.

    Provider writes are never retried here when their result may be ambiguous.
    Existing `unknown` mappings require manual reconciliation before retry.
    """
    context = await load_sync_run_context(settings, run_id)
    run, campaign = context["run"], context["campaign"]
    mapping = await create_campaign_mapping(settings, run_id)
    provider_campaign_id = mapping.get("instantly_campaign_id")
    if not provider_campaign_id:
        if mapping.get("status") == "failed":
            raise RuntimeError("Instantly campaign creation needs manual reconciliation before retry.")
        payload = build_campaign_payload(campaign, campaign["steps"],
                                         delay_hours=settings.instantly_followup_delay_hours)
        try:
            created = await create_campaign(payload, settings)
        except (InstantlyTimeoutError, InstantlyWriteError, BadResponseError) as exc:
            # Keep a safe provider diagnostic so an operator can distinguish a
            # rejected request (for example HTTP 400) from an ambiguous write.
            # The client exception never includes raw provider response bodies,
            # credentials, or lead/email content.
            if isinstance(exc, InstantlyWriteError) and not exc.outcome_unknown:
                detail = str(exc)[:240]
            else:
                detail = f"{str(exc)} Reconcile Instantly before retrying."[:240]
            await update_campaign_mapping(settings, mapping["id"], {
                "status": "failed", "error_detail": detail,
            })
            raise
        provider_campaign_id = created["id"]
        provider_status = created.get("status")
        if provider_status not in (None, "draft", 0):
            await update_campaign_mapping(settings, mapping["id"], {
                "instantly_campaign_id": provider_campaign_id, "status": "failed",
                "error_detail": "Instantly returned a non-draft campaign status; manual review is required.",
            })
            raise RuntimeError("Instantly campaign was not confirmed as an inactive draft; lead sync was stopped.")
        await update_campaign_mapping(settings, mapping["id"], {
            "instantly_campaign_id": provider_campaign_id, "status": "draft_created", "error_detail": None,
        })

    counts = {"synced": 0, "skipped": 0, "failed": 0, "unknown": 0}
    offset, page_size = 0, 500
    step_count = len(campaign["steps"])
    while True:
        leads = await load_sync_lead_page(settings, run_id, str(run["batch_id"]),
                                          offset=offset, limit=page_size)
        if not leads:
            break
        states = await get_lead_mapping_statuses(settings, mapping["id"], [str(row["id"]) for row in leads])
        candidates: list[dict] = []
        mapping_rows: list[dict] = []
        for lead in leads:
            previous = states.get(str(lead["id"]))
            if previous and previous.get("status") in {"synced", "skipped", "unknown", "failed"}:
                counts[previous["status"]] += 1
                continue
            emails = sorted(lead.get("personalized_steps", []), key=lambda item: item.get("step_number") or 0)
            # A lead with even one review-required, missing, or failed step is held out.
            if (len(emails) != step_count or any(item.get("status") != "queued" for item in emails)
                    or any(item.get("step_number") != index for index, item in enumerate(emails, start=1))):
                counts["skipped"] += 1
                continue
            try:
                provider_lead = build_lead_payload(lead, emails)
            except ValueError:
                counts["skipped"] += 1
                continue
            candidates.append(provider_lead)
            mapping_rows.append({"lead_id": str(lead["id"]), "status": "unknown",
                                 "error_detail": "Sync in progress; reconcile provider before retrying."})

        if candidates:
            await save_lead_mappings(settings, mapping["id"], mapping_rows)
            await update_campaign_mapping(settings, mapping["id"], {"status": "syncing_leads", "error_detail": None})
            result = await add_campaign_leads(provider_campaign_id, candidates, settings)
            returned = {int(item["index"]): item["id"] for item in result.get("created_leads", [])}
            resolved = []
            for index, row in enumerate(mapping_rows):
                provider_id = returned.get(index)
                if provider_id:
                    resolved.append({"lead_id": row["lead_id"], "status": "synced",
                                     "instantly_lead_id": provider_id,
                                     "synced_at": datetime.now(UTC).isoformat(),
                                     "error_detail": None})
                    counts["synced"] += 1
                else:
                    resolved.append({"lead_id": row["lead_id"], "status": "skipped",
                                     "instantly_lead_id": None, "error_detail": "Instantly skipped this lead during validation or deduplication."})
                    counts["skipped"] += 1
            await save_lead_mappings(settings, mapping["id"], resolved)
        offset += len(leads)
        if len(leads) < page_size:
            break

    unresolved = counts["unknown"] + counts["failed"]
    await update_campaign_mapping(settings, mapping["id"], {
        "status": "failed" if unresolved else "leads_synced",
        "error_detail": "Some provider lead outcomes need reconciliation; those leads will not be resent."
        if unresolved else None,
    })
    return {"run_id": run_id, "instantly_campaign_id": provider_campaign_id, **counts}
