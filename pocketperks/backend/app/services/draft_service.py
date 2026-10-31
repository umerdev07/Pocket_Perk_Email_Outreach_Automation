"""Generic draft generation and orchestration."""
import json

from app.config import Settings
from app.drafts_db import load_campaign, upsert_step_draft, get_draft_context
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
    return await upsert_step_draft(settings, draft["step_id"], payload["subject"].strip(), payload["body"].strip())
