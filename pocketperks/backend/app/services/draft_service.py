"""Generic draft generation and orchestration."""
import json

from app.config import Settings
from app.drafts_db import (demote_campaign, get_draft_context, load_campaign,
                           prepare_step_draft, set_generation_state, upsert_step_draft)
from app.integrations.openrouter_client import (
    BadResponseError, MissingAPIKeyError, MissingModelError, OpenRouterError,
    OpenRouterTimeoutError, RateLimitedError, chat_completion_json,
)


def _safe_failure(exc: Exception) -> tuple[str, str]:
    """Return a diagnostic that cannot contain credentials or raw provider payloads."""
    if isinstance(exc, MissingAPIKeyError):
        return "missing_api_key", "OpenRouter API key is not configured."
    if isinstance(exc, MissingModelError):
        return "missing_model", "OpenRouter model is not configured."
    if isinstance(exc, RateLimitedError):
        return "rate_limited", "OpenRouter rate limit persisted after retries."
    if isinstance(exc, OpenRouterTimeoutError):
        return "timeout", "OpenRouter request timed out."
    if isinstance(exc, BadResponseError):
        return "bad_openrouter_response", str(exc)
    if isinstance(exc, ValueError):
        return "invalid_draft_json", "Model response did not contain a usable subject and body."
    return "draft_save_or_generation_error", "Draft generation or database save failed. Check backend configuration and database access."


def _prompt(campaign: dict, step: dict, previous: list[dict]) -> list[dict]:
    previous_text = json.dumps(previous, ensure_ascii=False)
    system = (
        "Write a short, friendly, convincing email for an independent UK cafe or restaurant. "
        "Mention PocketPerks Loyalty Stamp and include one clear call to action. Use only facts and "
        "claims explicitly present in the campaign instructions and this step context. Do not invent "
        "offers, discounts, achievements, customers, or business facts. Do not include an unsubscribe line. "
        "Greet generically with exactly 'Hi there,'. Return only a JSON object with string keys subject and body."
    )
    user = (f"Campaign instructions: {campaign['instructions']}\n"
            f"Email step {step['step_number']} context: {step['context']}\n"
            f"Previous email subjects and bodies (maintain a coherent follow-up sequence): {previous_text}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def generate_campaign_drafts(settings: Settings, campaign_id: str) -> dict:
    campaign = await load_campaign(settings, campaign_id)
    if campaign is None:
        return {"error": "not_found"}
    steps = sorted(campaign.get("campaign_steps", []), key=lambda item: item["step_number"])
    created, failed, prior = [], [], []
    for step in steps:
        try:
            payload = await chat_completion_json(_prompt(campaign, step, prior), settings)
            if not isinstance(payload, dict) or not isinstance(payload.get("subject"), str) or not isinstance(payload.get("body"), str):
                raise ValueError("Model response must contain subject and body strings.")
            subject, body = payload["subject"].strip(), payload["body"].strip()
            if not subject or not body:
                raise ValueError("Model response subject and body must not be empty.")
            draft = await upsert_step_draft(settings, step["id"], subject, body)
            created.append(draft)
            prior.append({"subject": subject, "body": body})
        except Exception as exc:
            category, detail = _safe_failure(exc)
            failed.append({"step_number": step["step_number"], "step_id": step["id"],
                           "error": category, "detail": detail})
    return {"campaign_id": campaign_id, "generated": created, "failed": failed}


def _previous_steps(campaign: dict, step_number: int) -> list[dict]:
    previous = []
    for earlier in sorted(campaign.get("campaign_steps", []), key=lambda item: item["step_number"]):
        if earlier["step_number"] >= step_number:
            break
        relation = earlier.get("generic_drafts")
        if isinstance(relation, list):
            relation = relation[0] if relation else None
        if isinstance(relation, dict) and relation.get("subject") and relation.get("body"):
            previous.append({"subject": relation["subject"], "body": relation["body"]})
    return previous


async def _generate_one_step(settings: Settings, campaign: dict, step: dict) -> dict:
    payload = await chat_completion_json(_prompt(
        campaign, step, _previous_steps(campaign, int(step["step_number"]))), settings)
    if not isinstance(payload, dict) or not isinstance(payload.get("subject"), str) or not isinstance(payload.get("body"), str):
        raise ValueError("Model response must contain subject and body strings.")
    subject, body = payload["subject"].strip(), payload["body"].strip()
    if not subject or not body:
        raise ValueError("Model response subject and body must not be empty.")
    return await upsert_step_draft(settings, step["id"], subject, body)


async def queue_campaign_draft_generation(settings: Settings, campaign_id: str) -> dict:
    """Persist visible generating states before handing draft work to a background task."""
    campaign = await load_campaign(settings, campaign_id)
    if campaign is None:
        return {"error": "not_found"}
    steps = sorted(campaign.get("campaign_steps", []), key=lambda item: item["step_number"])
    for step in steps:
        await prepare_step_draft(settings, step["id"])
    return {"campaign_id": campaign_id, "generation_status": "generating", "total_steps": len(steps)}


async def run_campaign_draft_generation_job(settings: Settings, campaign_id: str) -> None:
    try:
        campaign = await load_campaign(settings, campaign_id)
        if campaign is None:
            return
        for step in sorted(campaign.get("campaign_steps", []), key=lambda item: item["step_number"]):
            relation = step.get("generic_drafts")
            if isinstance(relation, list):
                relation = relation[0] if relation else None
            draft_id = relation.get("id") if isinstance(relation, dict) else None
            try:
                await _generate_one_step(settings, campaign, step)
            except Exception as exc:
                if draft_id:
                    _category, safe_detail = _safe_failure(exc)
                    try:
                        await set_generation_state(settings, draft_id, "failed", safe_detail)
                    except Exception:
                        pass
            # Refresh so later messages can use the newly generated preceding step.
            campaign = await load_campaign(settings, campaign_id) or campaign
    except Exception:
        # If campaign reload itself fails, close all still-generating step states.
        try:
            campaign = await load_campaign(settings, campaign_id)
            for step in (campaign or {}).get("campaign_steps", []):
                relation = step.get("generic_drafts")
                if isinstance(relation, list):
                    relation = relation[0] if relation else None
                if isinstance(relation, dict) and relation.get("generation_status") == "generating":
                    await set_generation_state(settings, relation["id"], "failed",
                                               "Draft generation could not finish. Check backend/database connectivity, then retry.")
        except Exception:
            # Leave provider/database internals out of the user-visible state.
            pass


async def run_step_generation_job(settings: Settings, campaign_id: str,
                                  step_id: str, draft_id: str) -> None:
    try:
        campaign = await load_campaign(settings, campaign_id)
        step = next((item for item in (campaign or {}).get("campaign_steps", [])
                     if item.get("id") == step_id), None)
        if not campaign or not step:
            raise ValueError("Campaign email step was not found.")
        await _generate_one_step(settings, campaign, step)
    except Exception as exc:
        _category, safe_detail = _safe_failure(exc)
        try:
            await set_generation_state(settings, draft_id, "failed", safe_detail)
        except Exception:
            pass


async def regenerate_draft(settings: Settings, draft_id: str) -> dict | None:
    draft = await get_draft_context(settings, draft_id)
    if draft is None:
        return None
    step = draft.get("campaign_steps") or {}
    campaign = (step.get("campaigns") or {})
    # Regeneration includes all earlier step drafts for conversational continuity.
    whole = await load_campaign(settings, campaign.get("id", step.get("campaign_id", "")))
    previous = []
    for earlier in sorted((whole or {}).get("campaign_steps", []), key=lambda item: item["step_number"]):
        if earlier["step_number"] >= step.get("step_number", 0):
            break
        earlier_drafts = earlier.get("generic_drafts")
        if isinstance(earlier_drafts, list):
            earlier_drafts = earlier_drafts[0] if earlier_drafts else None
        if isinstance(earlier_drafts, dict):
            previous.append({"subject": earlier_drafts["subject"], "body": earlier_drafts["body"]})
    payload = await chat_completion_json(_prompt(campaign, step, previous), settings)
    if not isinstance(payload, dict) or not isinstance(payload.get("subject"), str) or not isinstance(payload.get("body"), str):
        raise ValueError("Model response must contain subject and body strings.")
    saved = await upsert_step_draft(settings, draft["step_id"], payload["subject"].strip(), payload["body"].strip())
    if campaign.get("status") == "approved":
        await demote_campaign(settings, campaign.get("id") or step.get("campaign_id"))
    return saved


async def run_regeneration_job(settings: Settings, draft_id: str) -> None:
    """Run regeneration after returning HTTP 202; persist safe failure state for polling."""
    try:
        result = await regenerate_draft(settings, draft_id)
        if result is None:
            return
    except Exception as exc:
        _category, safe_detail = _safe_failure(exc)
        try:
            await set_generation_state(settings, draft_id, "failed", safe_detail)
        except Exception:
            # Do not leak provider/database details into background-task logs.
            pass
