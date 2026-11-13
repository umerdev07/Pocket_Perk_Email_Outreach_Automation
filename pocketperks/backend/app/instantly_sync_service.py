"""Build safe Instantly draft and lead payloads from reviewed PocketPerks emails.

Campaigns are created as drafts, then optionally activated after every eligible
lead sequence has synced and the configured start time has arrived.
"""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo
from typing import Any

from app.config import Settings, get_settings
from app.lead_eligibility import lead_eligibility_reason
from app.instantly_sync_db import (create_campaign_mapping, get_lead_mapping_statuses,
                                   load_sync_run_context, load_sync_lead_page,
                                   save_lead_mappings, update_campaign_mapping)
from app.integrations.instantly_client import (BadResponseError, InstantlyError,
                                               InstantlyTimeoutError, InstantlyWriteError,
                                               activate_campaign, add_campaign_leads, create_campaign,
                                               list_campaign_leads, update_campaign_schedule)
from app.suppression_db import get_suppressed_emails
from app.services.risk_service import PLACEHOLDER_RE
from app.services.email_footer import append_outreach_footer

LEGACY_SCHEDULE_TIMEZONE = "Asia/Karachi"


def build_campaign_payload(campaign: dict[str, Any], steps: list[dict[str, Any]], *,
                           sender_emails: list[str] | None = None) -> dict[str, Any]:
    """Build one inactive Instantly sequence using campaign step delays and merge variables."""
    if not isinstance(campaign, dict) or not str(campaign.get("name", "")).strip():
        raise ValueError("Campaign name is required for Instantly sync.")
    start_at, end_at = campaign.get("start_at"), campaign.get("end_at")
    start_date, end_date = campaign.get("start_date"), campaign.get("end_date")
    start_local = end_local = None
    if start_at and end_at:
        try:
            start_dt = datetime.fromisoformat(str(start_at).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
            if start_dt.tzinfo is None or end_dt.tzinfo is None:
                raise ValueError
            schedule_timezone = ZoneInfo(str(campaign.get("timezone") or LEGACY_SCHEDULE_TIMEZONE))
            start_local = start_dt.astimezone(schedule_timezone)
            end_local = end_dt.astimezone(schedule_timezone)
            start = start_local.date()
            end = end_local.date()
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
    has_explicit_daily_window = bool(campaign.get("daily_send_start") and campaign.get("daily_send_end"))
    daily_start = str(campaign.get("daily_send_start") or
                      (start_local.strftime("%H:%M") if start_local else "09:00"))[:5]
    daily_end = str(campaign.get("daily_send_end") or
                    (end_local.strftime("%H:%M") if end_local else "17:00"))[:5]
    if daily_end <= daily_start:
        if has_explicit_daily_window:
            raise ValueError("Daily sending end time must be later than its start time.")
        # Preserve the old full-day provider schedule for legacy overnight
        # campaigns without the new explicit daily-window columns.
        daily_start, daily_end = "00:00", "23:59"
    configured_days = campaign.get("sending_days")
    if not isinstance(configured_days, list) or not configured_days:
        configured_days = [1, 2, 3, 4, 5]
    if any(type(day) is not int or day < 0 or day > 6 for day in configured_days):
        raise ValueError("Sending days must use Sunday=0 through Saturday=6.")
    if not isinstance(steps, list) or not steps:
        raise ValueError("At least one campaign step is required.")

    ordered = sorted(steps, key=lambda item: item.get("step_number", 0))
    sequence_steps = []
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(ordered, start=1):
        if step.get("step_number") != index:
            raise ValueError("Campaign steps must be numbered consecutively from 1.")
        trigger_type = step.get("trigger_type", "scheduled")
        if trigger_type not in {"scheduled", "after_conversation", "close_loop"}:
            raise ValueError("Campaign email step has an invalid trigger type.")
        if index == 1 and trigger_type != "scheduled":
            raise ValueError("Email 1 must be an automatically scheduled step.")
        delay_minutes = step.get("delay_minutes")
        if delay_minutes is None:
            legacy_days = step.get("delay_days")
            if legacy_days is None:
                delay_minutes = 0 if index == 1 else 4320
            else:
                if type(legacy_days) is not int or legacy_days < 0:
                    raise ValueError("Every campaign step needs a non-negative delay.")
                delay_minutes = legacy_days * 1440
        if type(delay_minutes) is not int or delay_minutes < (0 if index == 1 else 15) or delay_minutes > 43200:
            raise ValueError("Email 1 must have no delay; follow-up delay must be 15 minutes to 30 days.")
        if index == 1 and delay_minutes != 0:
            raise ValueError("The first campaign email must have a zero delay.")
        draft = step.get("draft")
        draft_approved = isinstance(draft, dict) and draft.get("status") == "approved"
        if step.get("status") != "approved" and step.get("draft_status") != "approved" and not draft_approved:
            raise ValueError("Every campaign step must have an approved generic draft.")
        normalized_steps.append({**step, "trigger_type": trigger_type,
                                 "_validated_delay_minutes": delay_minutes})

    # Instantly attaches a step's delay to the wait AFTER that email. PocketPerks
    # stores the user's wait on the follow-up step (e.g. Email 2 = wait after
    # Email 1), so shift each follow-up's delay one provider step earlier.
    # Manual conversation emails are excluded from the provider sequence.
    automatic_steps = [step for step in normalized_steps
                       if step["trigger_type"] != "after_conversation"]
    for index, step in enumerate(automatic_steps):
        next_step = automatic_steps[index + 1] if index + 1 < len(automatic_steps) else None
        delay_minutes = (next_step["_validated_delay_minutes"] if next_step is not None else 0)
        if delay_minutes % 1440 == 0:
            delay, delay_unit = delay_minutes // 1440, "days"
        elif delay_minutes % 60 == 0:
            delay, delay_unit = delay_minutes // 60, "hours"
        else:
            delay, delay_unit = delay_minutes, "minutes"
        sequence_steps.append({
            "type": "email",
            # The delay on this step controls the wait until the next step.
            "delay": delay,
            "delay_unit": delay_unit,
            "variants": [{
                "subject": "{{pp_email_" + str(step['step_number']) + "_subject}}",
                "body": "{{pp_email_" + str(step['step_number']) + "_body}}",
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
                "timing": {"from": daily_start, "to": daily_end},
                "days": {str(day): day in configured_days for day in range(7)},
                "timezone": str(campaign.get("timezone") or LEGACY_SCHEDULE_TIMEZONE),
            }],
        },
        "sequences": [{"steps": sequence_steps}],
        "stop_on_reply": True,
    }


def build_lead_payload(lead: dict[str, Any], personalized_steps: list[dict[str, Any]],
                       settings: Settings | None = None) -> dict[str, Any]:
    """Map a fully reviewed lead and its step copy to Instantly custom variables."""
    email = lead.get("email") if isinstance(lead, dict) else None
    if not isinstance(email, str) or not email.strip():
        raise ValueError("Lead needs an email address before Instantly sync.")
    if not isinstance(personalized_steps, list) or not personalized_steps:
        raise ValueError("Lead needs all personalized campaign steps before Instantly sync.")

    ordered = sorted(personalized_steps, key=lambda item: item.get("step_number", 0))
    variables: dict[str, str] = {}
    for step in ordered:
        index = step.get("step_number")
        if type(index) is not int or step.get("status") != "queued":
            raise ValueError("Only a complete sequence of queued emails can be synced.")
        subject, body = step.get("subject"), step.get("body")
        if not isinstance(subject, str) or not subject.strip() or not isinstance(body, str) or not body.strip():
            raise ValueError("Every queued email needs a subject and body.")
        if PLACEHOLDER_RE.search(subject) or PLACEHOLDER_RE.search(body):
            raise ValueError("Personalized email still contains an unresolved placeholder.")
        variables[f"pp_email_{index}_subject"] = subject.strip()
        variables[f"pp_email_{index}_body"] = append_outreach_footer(body.strip(), settings or get_settings())

    business_name = str(lead.get("business_name") or "").strip()
    custom_variables = {**variables}
    if business_name:
        # Keep a plainly named copy available in Instantly's custom columns as
        # well as its standard Company field, so operators can inspect it easily.
        custom_variables["pp_business_name"] = business_name
    return {
        "email": email.strip().lower(),
        "company_name": business_name,
        "first_name": str(lead.get("first_name") or ""),
        "website": str(lead.get("website") or ""),
        "phone": str(lead.get("phone") or ""),
        "custom_variables": custom_variables,
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


async def sync_completed_run(settings: Settings, run_id: str, pool=None) -> dict[str, Any]:
    """Create, sync and activate only after sender, data and schedule checks pass.

    Lead writes are chunked and their provider IDs are persisted independently.
    If a write response is ambiguous, read the campaign back before considering
    another write; an unavailable read leaves only that bounded chunk unknown.
    """
    context = await load_sync_run_context(settings, run_id,pool=pool)
    run, campaign = context["run"], context["campaign"]
    sender_emails = list(dict.fromkeys(email.strip().lower()
                                      for email in run.get("sender_account_emails", [])
                                      if isinstance(email, str) and email.strip()))
    if not sender_emails:
        raise RuntimeError("No sending inbox was selected before this run; no Instantly campaign was created.")
    mapping = await create_campaign_mapping(settings, run_id, sender_emails,pool=pool)
    mapped_senders = list(dict.fromkeys(email.strip().lower()
                                        for email in mapping.get("sender_account_emails", [])
                                        if isinstance(email, str) and email.strip()))
    if mapped_senders != sender_emails:
        raise RuntimeError("This run has a different saved Instantly sender selection; reconcile it before retrying.")
    provider_campaign_id = mapping.get("instantly_campaign_id")
    provider_campaign_was_active = mapping.get("status") == "active"
    campaign_was_created = False
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
            "No eligible leads synced to Instantly.",
        }
    )
    if mapping.get("status") == "failed" and not retryable_sync_failure:
        raise RuntimeError("Instantly campaign mapping is failed; reconcile provider state before retrying.")
    campaign_payload = build_campaign_payload(campaign, campaign["steps"],
                                              sender_emails=sender_emails)
    if not provider_campaign_id:
        if mapping.get("status") == "failed":
            raise RuntimeError("Instantly campaign creation needs manual reconciliation before retry.")
        try:
            created = await create_campaign(campaign_payload, settings)
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
            },pool=pool)
            raise
        provider_campaign_id = created["id"]
        provider_status = created.get("status")
        if provider_status not in (None, "draft", 0):
            await update_campaign_mapping(settings, mapping["id"], {
                "instantly_campaign_id": provider_campaign_id, "status": "failed",
                "error_detail": "Instantly returned a non-draft campaign status; manual review is required.",
            },pool=pool)
            raise RuntimeError("Instantly campaign was not confirmed as an inactive draft; lead sync was stopped.")
        await update_campaign_mapping(settings, mapping["id"], {
            "instantly_campaign_id": provider_campaign_id, "status": "draft_created", "error_detail": None,
        },pool=pool)
        campaign_was_created = True

    counts = {"synced": 0, "skipped": 0, "failed": 0, "unknown": 0}
    held_for_review = 0
    offset, page_size, provider_batch_size = 0, 500, 100
    automatic_steps = [step for step in campaign["steps"] if step.get("trigger_type", "scheduled") != "after_conversation"]
    step_count = len(automatic_steps)
    while True:
        leads = await load_sync_lead_page(settings, run_id, str(run["batch_id"]),
                                          offset=offset, limit=page_size,pool=pool)
        if not leads:
            break
        suppressed_emails = await get_suppressed_emails(settings, {
            str(lead.get("email") or "") for lead in leads
        },pool=pool)
        states = await get_lead_mapping_statuses(settings, mapping["id"], [str(row["id"]) for row in leads],pool=pool)
        candidates: list[dict] = []
        mapping_rows: list[dict] = []
        held_mapping_rows: list[dict] = []
        suppressed_mapping_rows: list[dict] = []
        for lead in leads:
            previous = states.get(str(lead["id"]))
            if previous and previous.get("status") in {"synced", "skipped", "failed"}:
                counts[previous["status"]] += 1
                continue
            eligibility_reason = lead_eligibility_reason(lead, getattr(settings, "lead_eligibility_mode", "advisory"))
            if eligibility_reason:
                counts["skipped"] += 1
                held_for_review += 1
                # Keep a pending mapping so an operator can correct an address or
                # verification metadata and the recovery worker can reassess it.
                held_mapping_rows.append({"lead_id": str(lead["id"]), "status": "pending",
                                          "error_detail": eligibility_reason})
                continue
            if str(lead.get("email") or "").strip().lower() in suppressed_emails:
                counts["skipped"] += 1
                suppressed_mapping_rows.append({"lead_id": str(lead["id"]), "status": "skipped",
                                                "error_detail": "Recipient opted out; address is on the global suppression list."})
                continue
            emails = sorted(lead.get("personalized_steps", []), key=lambda item: item.get("step_number") or 0)
            automatic_emails = [item for item in emails if item.get("trigger_type", "scheduled") != "after_conversation"]
            # A lead with even one review-required, missing, or failed step is held out.
            if (len(automatic_emails) != step_count
                    or any(item.get("status") != "queued" for item in automatic_emails)):
                counts["skipped"] += 1
                held_for_review += 1
                held_mapping_rows.append({"lead_id": str(lead["id"]), "status": "pending",
                                          "error_detail": "Held until every campaign email step is queued after review."})
                continue
            try:
                provider_lead = build_lead_payload(lead, automatic_emails, settings)
            except ValueError as exc:
                counts["skipped"] += 1
                held_for_review += 1
                reason = ("Held because a personalized email still contains a placeholder; edit it and re-approve."
                          if "placeholder" in str(exc).lower() else
                          "Held because the complete personalized email sequence is not ready for sync.")
                held_mapping_rows.append({"lead_id": str(lead["id"]), "status": "pending",
                                          "error_detail": reason})
                continue
            candidates.append(provider_lead)
            mapping_rows.append({"lead_id": str(lead["id"]), "status": "unknown",
                                 "error_detail": "Sync in progress; reconcile provider before retrying."})

        if suppressed_mapping_rows:
            await save_lead_mappings(settings, mapping["id"], suppressed_mapping_rows,pool=pool)
        if held_mapping_rows:
            await save_lead_mappings(settings, mapping["id"], held_mapping_rows,pool=pool)
        if candidates:
            await save_lead_mappings(settings, mapping["id"], mapping_rows,pool=pool)
            await update_campaign_mapping(settings, mapping["id"], {
                "status": "active" if provider_campaign_was_active else "syncing_leads",
                "error_detail": None,
            },pool=pool)
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
                await save_lead_mappings(settings, mapping["id"], list(resolved_by_email.values()),pool=pool)
        offset += len(leads)
        if len(leads) < page_size:
            break

    unresolved = counts["unknown"] + counts["failed"]
    waiting_for_review = held_for_review > 0 and counts["synced"] == 0 and not unresolved
    if counts["synced"] > 0 and not provider_campaign_was_active and not campaign_was_created:
        # Existing drafts may have been created by an older backend with a
        # default send window. Align the provider schedule before auto-launch.
        await update_campaign_schedule(provider_campaign_id, campaign_payload["name"],
                                       campaign_payload["campaign_schedule"], settings)
    await update_campaign_mapping(settings, mapping["id"], {
        "status": "active" if provider_campaign_was_active else "failed" if unresolved
        else "awaiting_review" if waiting_for_review else "leads_synced",
        "error_detail": ("Some provider lead outcomes need reconciliation; they will be checked before retry."
                         if unresolved else "Waiting for personalized email review before lead sync."
                         if waiting_for_review else None),
    },pool=pool)
    result: dict[str, Any] = {"run_id": run_id, "instantly_campaign_id": provider_campaign_id, **counts}
    if unresolved:
        result["launch"] = "held_for_reconciliation"
        return result
    if waiting_for_review:
        result["launch"] = "awaiting_review"
        return result
    if provider_campaign_was_active:
        result["launch"] = "already_active"
        return result
    if counts["synced"] < 1:
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "failed", "error_detail": "No eligible leads synced to Instantly.",
        },pool=pool)
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
        },pool=pool)
        result["launch"] = "held_invalid_schedule"
        return result
    now = datetime.now(UTC)
    if end_at <= start_at or end_at <= now:
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "failed", "error_detail": "Campaign sending window has ended or is invalid.",
        },pool=pool)
        result["launch"] = "held_invalid_schedule"
        return result
    if start_at > now:
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "scheduled", "sender_account_emails": sender_emails, "error_detail": None,
        },pool=pool)
        result["launch"] = "scheduled"
        return result
    await update_campaign_mapping(settings, mapping["id"], {
        "status": "launching", "error_detail": "Selected sender inboxes saved; activation is being confirmed.",
    },pool=pool)
    try:
        await activate_campaign(provider_campaign_id, settings)
    except (InstantlyError, InstantlyTimeoutError, InstantlyWriteError) as exc:
        unknown = getattr(exc, "outcome_unknown", False)
        await update_campaign_mapping(settings, mapping["id"], {
            "status": "launching" if unknown else "failed",
            "error_detail": "Instantly activation outcome is unknown; reconcile before retrying."
            if unknown else f"Instantly activation failed: {str(exc)}"[:240],
        },pool=pool)
        result["launch"] = "reconciliation_required" if unknown else "failed"
        return result
    await update_campaign_mapping(settings, mapping["id"], {"status": "active", "error_detail": None},pool=pool)
    result["launch"] = "active"
    return result
