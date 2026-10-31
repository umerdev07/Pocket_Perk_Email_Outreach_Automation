"""Chunked, bounded-concurrency personalization for a campaign run."""
import asyncio
import json
from collections.abc import Iterator

from fastapi import HTTPException

from app.config import Settings
from app.integrations.openrouter_client import OpenRouterError, chat_completion_json
from app.personalization_db import load_lead_page, load_run_context, save_personalized_email, update_run_progress
from app.services.risk_service import assess_personalized_email


def _personalization_messages(campaign: dict, step: dict, lead: dict) -> list[dict]:
    system = (
        "Personalize the approved email draft for one UK independent cafe or restaurant. Preserve its meaning, "
        "keep it concise and friendly, and retain a clear call to action. Use only the facts in the approved draft "
        "and supplied lead fields. Do not invent offers, discounts, achievements, customers, or business facts. "
        "Do not add an unsubscribe line. Return only a JSON object with string subject and body fields."
    )
    user = {
        "campaign_instructions": campaign["instructions"],
        "email_step_context": step["context"],
        "approved_generic_draft": {"subject": step["draft"]["subject"], "body": step["draft"]["body"]},
        "lead_data": {
            "business_name": lead.get("business_name"), "business_category": lead.get("business_category"),
            "city": lead.get("city"), "website": lead.get("website"),
        },
    }
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def _chunks(items: list[dict], size: int) -> Iterator[list[dict]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _safe_step_error(exc: Exception) -> str:
    """Expose useful known errors without leaking raw request/provider details."""
    if isinstance(exc, OpenRouterError):
        return str(exc)[:300]
    if isinstance(exc, HTTPException) and isinstance(exc.detail, str):
        return exc.detail[:300]
    if isinstance(exc, (ValueError, TypeError)):
        return str(exc)[:300]
    return f"Unexpected {type(exc).__name__} while generating or saving this step."


async def _process_lead(settings: Settings, run_id: str, campaign: dict, steps: list[dict],
                        lead: dict, semaphore: asyncio.Semaphore) -> tuple[str, dict | None]:
    if not (lead.get("business_name") or "").strip():
        return "skipped", {"lead_id": lead["id"], "reason": "missing_business_name"}

    failed_steps = []
    # Concurrency is bounded per lead, while the email sequence for each lead
    # remains ordered. This avoids bursts against the free model's rate limit.
    async with semaphore:
        for step in steps:
            try:
                result = await chat_completion_json(_personalization_messages(campaign, step, lead), settings)
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON object.")
                subject, body = result.get("subject"), result.get("body")
                if not isinstance(subject, str) or not subject.strip() or not isinstance(body, str) or not body.strip():
                    raise ValueError("Generated subject and body must be non-empty strings.")
                draft = {"subject": step["draft"]["subject"], "body": step["draft"]["body"]}
                risk = await assess_personalized_email(subject.strip(), body.strip(), draft=draft, lead=lead,
                                                       settings=settings, campaign=campaign, step=step)
                await save_personalized_email(settings, {
                    "run_id": run_id, "lead_id": lead["id"], "campaign_id": campaign["id"],
                    "step_id": step["id"], "subject": subject.strip(), "body": body.strip(),
                    "risk_level": risk["risk_level"], "risk_reason": risk["risk_reason"],
                    "status": "queued" if risk["risk_level"] == "LOW" else "pending_review",
                })
            except Exception as exc:
                # A failed step marks this lead failed, but doesn't block its other
                # steps or any other lead in this chunk.
                failed_steps.append({"lead_id": lead["id"], "business_name": lead.get("business_name"),
                                     "step_number": step.get("step_number"), "reason": _safe_step_error(exc)})
    return ("failed" if failed_steps else "processed"), failed_steps or None


async def process_campaign_run(settings: Settings, run_id: str) -> None:
    processed = skipped = failed = 0
    skip_log: list[dict] = []
    failure_log: list[dict] = []
    try:
        data = await load_run_context(settings, run_id)
        campaign, steps = data["campaign"], data["campaign"]["steps"]
        semaphore = asyncio.Semaphore(settings.personalization_concurrency)

        async def process_chunk(lead_chunk: list[dict]) -> None:
            nonlocal processed, skipped, failed
            results = await asyncio.gather(*(
                _process_lead(settings, run_id, campaign, steps, lead, semaphore)
                for lead in lead_chunk
            ), return_exceptions=True)

            for lead, outcome in zip(lead_chunk, results, strict=True):
                if isinstance(outcome, Exception):
                    failed += 1
                    failure_log.append({"lead_id": lead.get("id"), "business_name": lead.get("business_name"),
                                        "step_number": None, "reason": _safe_step_error(outcome)})
                elif outcome[0] == "skipped":
                    skipped += 1
                    if outcome[1]:
                        skip_log.append(outcome[1])
                elif outcome[0] == "failed":
                    failed += 1
                    if outcome[1]:
                        failure_log.extend(outcome[1])
                else:
                    processed += 1

            # One progress write per completed chunk instead of one per lead.
            await update_run_progress(settings, run_id, processed=processed, skipped=skipped, failed=failed,
                                      skip_log=None, status="running")

        # Existing fixtures may supply a lead list. Production reads bounded
        # database pages and then schedules only a small chunk at a time.
        if "leads" in data:
            for lead_chunk in _chunks(data["leads"], settings.personalization_batch_size):
                await process_chunk(lead_chunk)
        else:
            batch_id = data["run"]["batch_id"]
            db_page_size = 500
            offset = 0
            while True:
                page = await load_lead_page(settings, batch_id, offset=offset, limit=db_page_size)
                for lead_chunk in _chunks(page, settings.personalization_batch_size):
                    await process_chunk(lead_chunk)
                offset += len(page)
                if len(page) < db_page_size:
                    break

        try:
            await update_run_progress(settings, run_id, processed=processed, skipped=skipped, failed=failed,
                                      skip_log=skip_log, status="completed", finished=True, failure_log=failure_log)
        except HTTPException:
            # A not-yet-applied optional failure-log migration must not turn a
            # successfully processed run into a failed run.
            await update_run_progress(settings, run_id, processed=processed, skipped=skipped, failed=failed,
                                      skip_log=skip_log, status="completed", finished=True)
    except Exception as exc:
        # The request has already returned. Record fatal setup/database errors on the run.
        # Only expose known application messages or the exception type; provider/HTTP
        # exception text can contain request details and must not be persisted.
        detail = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else (
            f"Background processing stopped ({type(exc).__name__}). Check database schema, access, and backend logs."
        )
        try:
            await update_run_progress(settings, run_id, processed=processed, skipped=skipped, failed=failed + 1,
                                      skip_log=None, status="failed", finished=True, error_detail=detail,
                                      failure_log=failure_log)
        except Exception:
            # Keep the failure status useful even if optional diagnostic columns or
            # skip-log migrations have not yet been applied in Supabase.
            try:
                await update_run_progress(settings, run_id, processed=processed, skipped=skipped, failed=failed + 1,
                                          skip_log=None, status="failed", finished=True)
            except Exception:
                pass
