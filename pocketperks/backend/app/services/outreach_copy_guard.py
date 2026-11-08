"""Quality guardrails that keep internal references out of customer-facing copy."""
import json
import re

from app.integrations.claude_client import chat_completion_json


class OutreachCopyValidationError(RuntimeError):
    """Generated copy still violates a customer-facing content rule after correction."""


_SECTOR_ALIASES = {
    "cafe": (r"caf[eé]s?", r"coffee\s*shops?", r"coffeehouses?"),
    "barber": (r"barbers?", r"barber\s*shops?", r"barbershops?"),
    "restaurant": (r"restaurants?", r"bistros?", r"dining\s+businesses"),
    "sandwich_shop": (r"sandwich\s*shops?", r"sandwich\s+bars?"),
    "pub": (r"pubs?", r"public\s+houses?"),
}
_SECTOR_PATTERNS = {
    sector: re.compile(r"\b(?:" + "|".join(aliases) + r")\b", re.I)
    for sector, aliases in _SECTOR_ALIASES.items()
}
_INTERNAL_SOURCE_PATTERNS = (
    re.compile(r"\bharrogate\s+outreach\s+kit\b", re.I),
    re.compile(r"\boutreach\s+kit\b", re.I),
    re.compile(r"\b(?:as\s+part\s+of|according\s+to|based\s+on)\s+(?:our\s+)?(?:internal\s+)?(?:campaign\s+)?(?:kit|brief|prompt|reference\s+(?:file|document))\b", re.I),
    re.compile(r"\b(?:campaign\s+instructions|internal\s+notes|source\s+document|reference\s+document)\b", re.I),
)


def has_internal_source_reference(subject: str, body: str) -> bool:
    text = f"{subject}\n{body}"
    return any(pattern.search(text) for pattern in _INTERNAL_SOURCE_PATTERNS)


def _sector_for_category(category: object) -> str | None:
    value = str(category or "").casefold().replace("&", "and")
    matches = [sector for sector, pattern in _SECTOR_PATTERNS.items() if pattern.search(value)]
    return matches[0] if len(matches) == 1 else None


def _campaign_sector(campaign: dict, step: dict) -> str | None:
    # A generic draft is sector-specific only when its own brief names exactly
    # one supported audience. Multi-sector and unspecified briefs stay neutral.
    text = " ".join(str(campaign.get(key) or "") for key in ("name", "instructions"))
    text += " " + str(step.get("context") or "")
    matches = {sector for sector, pattern in _SECTOR_PATTERNS.items() if pattern.search(text)}
    return next(iter(matches)) if len(matches) == 1 else None


def _copy_sector_issue(subject: str, body: str, *, campaign: dict,
                       step: dict, lead: dict | None) -> str | None:
    text = f"{subject}\n{body}"
    business_name = str((lead or {}).get("business_name") or "")
    if business_name:
        text = re.sub(re.escape(business_name), " ", text, flags=re.I)
    mentioned = {sector for sector, pattern in _SECTOR_PATTERNS.items() if pattern.search(text)}
    expected = _sector_for_category((lead or {}).get("business_category")) if lead else _campaign_sector(campaign, step)
    if not mentioned:
        return None
    if expected is None:
        return "Keep the copy sector-neutral because no single verified audience sector is specified."
    if mentioned - {expected}:
        return "Remove sector references that do not match the campaign audience or verified lead category."
    return None


def validate_outreach_copy(subject: str, body: str, *, campaign: dict,
                           step: dict, lead: dict | None = None) -> str | None:
    if has_internal_source_reference(subject, body):
        return "Remove internal kit, source, prompt, or campaign-brief references from recipient-facing copy."
    return _copy_sector_issue(subject, body, campaign=campaign, step=step, lead=lead)


async def generate_validated_copy(messages: list[dict], settings, *, campaign: dict,
                                  step: dict, lead: dict | None = None,
                                  generator=chat_completion_json) -> tuple[str, str]:
    """Generate once, request one correction for policy issues, then fail closed."""
    request = list(messages)
    last_issue = None
    for attempt in range(2):
        payload = await generator(request, settings)
        if not isinstance(payload, dict) or not isinstance(payload.get("subject"), str) or not isinstance(payload.get("body"), str):
            raise ValueError("Model response must contain subject and body strings.")
        subject, body = payload["subject"].strip(), payload["body"].strip()
        if not subject or not body:
            raise ValueError("Model response subject and body must not be empty.")
        last_issue = validate_outreach_copy(subject, body, campaign=campaign, step=step, lead=lead)
        if last_issue is None:
            return subject, body
        if attempt == 0:
            request = request + [
                {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
                {"role": "user", "content": (
                    f"Revise the subject and body to fix this content issue: {last_issue} "
                    "Keep the intended message and product facts. Return only the corrected JSON object."
                )},
            ]
    raise OutreachCopyValidationError(
        "Generated copy did not pass PocketPerks content checks after one correction."
    )
