"""Build safe Instantly draft and lead payloads from reviewed PocketPerks emails.

Campaigns are created as drafts, then optionally activated after every eligible
lead sequence has synced and the configured start time has arrived.
"""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo
from typing import Any

from app.config import Settings
from app.instantly_sync_db import (create_campaign_mapping, get_lead_mapping_statuses,
                                   load_sync_run_context, load_sync_lead_page,
                                   save_lead_mappings, update_campaign_mapping)
from app.integrations.instantly_client import (BadResponseError, InstantlyError,
                                               InstantlyTimeoutError, InstantlyWriteError,
                                               activate_campaign, add_campaign_leads, create_campaign,
                                               list_campaign_leads)
from app.suppression_db import get_suppressed_emails

LEGACY_SCHEDULE_TIMEZONE = "Asia/Karachi"


def build_campaign_payload(campaign: dict[str, Any], steps: list[dict[str, Any]], *,
                           delay_hours: int = 4,
                           sender_emails: list[str] | None = None) -> dict[str, Any]:
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
            schedule_timezone = ZoneInfo(str(campaign.get("timezone") or LEGACY_SCHEDULE_TIMEZONE))
            start = start_dt.astimezone(schedule_timezone).date()
            end = end_dt.astimezone(schedule_timezone).date()
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
        **({"email_list": list(dict.fromkeys(email.strip().lower() for email in sender_emails
                                               if isinstance(email, str) and email.strip()))}
           if sender_emails else {}),
        "is_evergreen": False,
        "campaign_schedule": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "schedules": [{
                "name": "PocketPerks UK weekdays",
                "timing": {"from": "09:00", "to": "17:00"},
                "days": {"0": False, "1": True, "2": True, "3": True,
                         "4": True, "5": True, "6": False},
                "timezone": str(campaign.get("timezone") or LEGACY_SCHEDULE_TIMEZONE),
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
        body_text = body.strip()
        opt_out_line = "To stop future PocketPerks emails, reply STOP."
        if "reply stop" not in body_text.lower() and "unsubscribe" not in body_text.lower():
            body_text = f"{body_text}\n\n{opt_out_line}"
        variables[f"pp_email_{index}_body"] = body_text

    business_name = lead.get("business_name") or ""
    return {
        "email": email.strip().lower(),
        "company_name": str(business_name),
        "first_name": str(business_name),
        "website": str(lead.get("website") or ""),
        "phone": str(lead.get("phone") or ""),
        "custom_variables": variables,
    }


def _aware_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Campaign {label} timestamp is required for automatic launch.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Campaign {label} timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Campaign {label} timestamp must include a timezone.")
    return parsed.astimezone(UTC)


async def sync_completed_run(settings: Settings, run_id: str) -> dict[str, Any]:
    """Create, sync and activate only after sender, data and schedule checks pass.

    Lead writes are chunked and their provider IDs are persisted independently.
    If a write response is ambiguous, read the campaign back before considering
    another write; an unavailable read leaves only that bounded chunk unknown.
    """
    context = await load_sync_run_context(settings, run_id)
    run, campaign = context["run"], context["campaign"]
    sender_emails = list(dict.fromkeys(email.strip().lower()
                                      for email in run.get("sender_account_emails", [])
                                      if isinstance(email, str) and email.strip()))
    if not sender_emails:
        raise RuntimeError("No sending inbox was selected before this run; no Instantly campaign was created.")
    mapping = await create_campaign_mapping(settings, run_id, sender_emails)
    mapped_senders = list(dict.fromkeys(email.strip().lower()
                                        for email in mapping.get("sender_account_emails", [])
                                        if isinstance(email, str) and email.strip()))
    if mapped_senders != sender_emails:
        raise RuntimeError("This run has a different saved Instantly sender selection; reconcile it before retrying.")
    provider_campaign_id = mapping.get("instantly_campaign_id")
    provider_campaign_was_active = mapping.get("status") == "active"
    if mapping.get("status") == "launching":
        return {"run_id": run_id, "instantly_campaign_id": provider_campaign_id,
                "synced": 0, "skipped": 0, "failed": 0, "unknown": 0,
                "launch": "reconciliation_required"}
    if mapping.get("status") == "completed":
        return {"run_id": run_id, "instantly_campaign_id": provider_campaign_id,
                "synced": 0, "skipped": 0, "failed": 0, "unknown": 0, "launch": "completed"}
    retryable_sync_failure = (
        mapping.get("status") == "failed"
        and bool(provider_campaign_id)
        and mapping.get("error_detail") in {
            "Some provider lead outcomes need reconciliation; those leads will not be resent.",
            "Some provider lead outcomes need reconciliation; they will be checked before retry.",
        }
    )
    if mapping.get("status") == "failed" and not retryable_sync_failure:
        raise RuntimeError("Instantly campaign mapping is failed; reconcile provider state before retrying.")
    if not provider_campaign_id:
        if mapping.get("status") == "failed":
            raise RuntimeError("Instantly campaign creation needs manual reconciliation before retry.")
        payload = build_campaign_payload(campaign, campaign["steps"],
                                         delay_hours=settings.instantly_followup_delay_hours,
                                         sender_emails=sender_emails)
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
    offset, page_size, provider_batch_size = 0, 500, 100
    step_count = len(campaign["steps"])
    while True:
        leads = await load_sync_lead_page(settings, run_id, str(run["batch_id"]),
                                          offset=offset, limit=page_size)
        if not leads:
            break
        suppressed_emails = await get_suppressed_emails(settings, {
            str(lead.get("email") or "") for lead in leads
        })
        states = await get_lead_mapping_statuses(settings, mapping["id"], [str(row["id"]) for row in leads])
        candidates: list[dict] = []
        mapping_rows: list[dict] = []
        held_mapping_rows: list[dict] = []
        suppressed_mapping_rows: list[dict] = []
        for lead in leads:
            previous = states.get(str(lead["id"]))
            if previous and previous.get("status") in {"synced", "skipped", "failed"}:
                counts[previous["status"]] += 1
                continue
            if str(lead.get("email") or "").strip().lower() in suppressed_emails:
                counts["skipped"] += 1
                suppressed_mapping_rows.append({"lead_id": str(lead["id"]), "status": "skipped",
                                                "error_detail": "Recipient opted out; address is on the global suppression list."})
                continue
            emails = sorted(lead.get("personalized_steps", []), key=lambda item: item.get("step_number") or 0)
            # A lead with even one review-required, missing, or failed step is held out.
            if (len(emails) != step_count or any(item.get("status") != "queued" for item in emails)
                    or any(item.get("step_number") != index for index, item in enumerate(emails, start=1))):
                counts["skipped"] += 1
                held_mapping_rows.append({"lead_id": str(lead["id"]), "status": "pending",
                                          "error_detail": "Held until every campaign email step is queued after review."})
                continue
            try:
                provider_lead = build_lead_payload(lead, emails)
            except ValueError:
                counts["skipped"] += 1
                held_mapping_rows.append({"lead_id": str(lead["id"]), "status": "pending",
                                          "error_detail": "Held because the complete personalized email sequence is not ready for sync."})
                continue
            candidates.append(provider_lead)
            mapping_rows.append({"lead_id": str(lead["id"]), "status": "unknown",
                                 "error_detail": "Sync in progress; reconcile provider before retrying."})

        if suppressed_mapping_rows:
            await save_lead_mappings(settings, mapping["id"], suppressed_mapping_rows)
        if held_mapping_rows:
            await save_lead_mappings(settings, mapping["id"], held_mapping_rows)
        if candidates:
            await save_lead_mappings(settings, mapping["id"], mapping_rows)
            await update_campaign_mapping(settings, mapping["id"], {
                "status": "active" if provider_campaign_was_active else "syncing_leads",
                "error_detail": None,
            })
            for batch_start in range(0, len(candidates), provider_batch_size):
                batch_candidates = candidates[batch_start:batch_start + provider_batch_size]
                batch_rows = mapping_rows[batch_start:batch_start + provider_batch_size]
                batch_emails = [item["email"] for item in batch_candidates]
                previously_unknown = [item["email"] for item, row in zip(batch_candidates, batch_rows)
                                      if states.get(str(row["lead_id"]), {}).get("status") == "unknown"]
                found: dict[str, str] = {}
                if previously_unknown:
                    try:
                        found = await list_campaign_leads(provider_campaign_id, previously_unknown, settings)
                    except InstantlyError:
                        # An unavailable read scope or transient read error must
                        # never cause an uncertain lead to be posted again.
                        counts["unknown"] += len(previously_unknown)
                        continue

                resolved_by_email: dict[str, dict] = {}
                pending: list[tuple[dict, dict]] = []
                for item, row in zip(batch_candidates, batch_rows):
                    email = item["email"].strip().lower()
                    provider_id = found.get(email)
                    if provider_id:
                        resolved_by_email[email] = {
                            "lead_id": row["lead_id"], "status": "synced",
                            "instantly_lead_id": provider_id,
                            "synced_at": datetime.now(UTC).isoformat(), "error_detail": None,
                        }
                        counts["synced"] += 1
                    else:
                        pending.append((item, row))

                if pending:
                    try:
                        result = await add_campaign_leads(
                            provider_campaign_id, [item for item, _ in pending], settings)
                    except InstantlyError as exc:
                        pending_emails = [item["email"].strip().lower() for item, _ in pending]
                        reconciled: dict[str, str] = {}
                        try:
                            reconciled = await list_campaign_leads(provider_campaign_id, pending_emails, settings)
                        except InstantlyError:
                            pass
                        for item, row in pending:
                            email = item["email"].strip().lower()
                            provider_id = reconciled.get(email)
                            if provider_id:
                                resolved_by_email[email] = {
                                    "lead_id": row["lead_id"], "status": "synced",
                                    "instantly_lead_id": provider_id,
                                    "synced_at": datetime.now(UTC).isoformat(), "error_detail": None,
                                }
                                counts["synced"] += 1
                            elif getattr(exc, "outcome_unknown", False):
                                resolved_by_email[email] = {
                                    "lead_id": row["lead_id"], "status": "unknown",
                                    "error_detail": "Instantly did not confirm this lead; it will be checked before any retry.",
                                }
                                counts["unknown"] += 1
                            else:
                                resolved_by_email[email] = {
                                    "lead_id": row["lead_id"], "status": "failed",
                                    "error_detail": f"Instantly rejected this lead batch: {type(exc).__name__}.",
                                }
                                counts["failed"] += 1
                    else:
                        returned = {int(item["index"]): item["id"]
                                    for item in result.get("created_leads", [])}
                        for index, (item, row) in enumerate(pending):
                            email = item["email"].strip().lower()
                            provider_id = returned.get(index)
                            if provider_id:
                                resolved_by_email[email] = {
                                    "lead_id": row["lead_id"], "status": "synced",
                                    "instantly_lead_id": provider_id,
                                    "synced_at": datetime.now(UTC).isoformat(), "error_detail": None,
                                }
                                counts["synced"] += 1
                            else:
                                resolved_by_email[email] = {
                                    "lead_id": row["lead_id"], "status": "skipped",
                                    "instantly_lead_id": None,
                                    "error_detail": "Instantly accepted the batch but skipped this lead during validation or deduplication.",
                                }
                                counts["skipped"] += 1
                await save_lead_mappings(settings, mapping["id"], list(resolved_by_email.values()))
        offset += len(leads)
        if len(leads) < page_size:
            break

    unresolved = counts["unknown"] + counts["failed"]
    await update_campaign_mapping(settings, mapping["id"], {
        "status": "active" if provider_campaign_was_active else "failed" if unresolved else "leads_synced",
        "error_detail": "Some provider lead outcomes need reconciliation; they will be checked before retry."
        if unresolved else None,
    })
    result: dict[str, Any] = {"run_id": run_id, "instantly_campaign_id": provider_campaign_id, **counts}
    if unresolved:
        result["launch"] = "held_for_reconciliation"
        return result
    if provider_campaign_was_active:
        result["launch"] = "already_active"
        return result
    if counts["synced"] < 1:
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "failed", "error_detail": "No eligible leads synced to Instantly.",
        })
        result["launch"] = "held_no_eligible_leads"
        return result
    if not settings.instantly_auto_launch or settings.send_mode != "live":
        result["launch"] = "disabled_by_configuration"
        return result
    try:
        start_at = _aware_datetime(campaign.get("start_at"), "start")
        end_at = _aware_datetime(campaign.get("end_at"), "end")
    except ValueError as exc:
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "failed", "error_detail": str(exc)[:240],
        })
        result["launch"] = "held_invalid_schedule"
        return result
    now = datetime.now(UTC)
    if end_at <= start_at or end_at <= now:
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "failed", "error_detail": "Campaign sending window has ended or is invalid.",
        })
        result["launch"] = "held_invalid_schedule"
        return result
    if start_at > now:
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "scheduled", "sender_account_emails": sender_emails, "error_detail": None,
        })
        result["launch"] = "scheduled"
        return result
    await update_campaign_mapping(settings, mapping["id"], {
        "status": "launching", "error_detail": "Selected sender inboxes saved; activation is being confirmed.",
    })
    try:
        await activate_campaign(provider_campaign_id, settings)
    except (InstantlyError, InstantlyTimeoutError, InstantlyWriteError) as exc:
        unknown = getattr(exc, "outcome_unknown", False)
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "launching" if unknown else "failed",
            "error_detail": "Instantly activation outcome is unknown; reconcile before retrying."
            if unknown else f"Instantly activation failed: {str(exc)}"[:240],
        })
        result["launch"] = "reconciliation_required" if unknown else "failed"
        return result
    await update_campaign_mapping(settings, mapping["id"], {"status": "active", "error_detail": None})
    result["launch"] = "active"
    return result
