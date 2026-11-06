"""Conservative eligibility checks for lead email readiness."""
import re


_VALID_RESULTS = {"valid", "verified", "deliverable", "safe", "valid_email"}
_INVALID_RESULTS = {"invalid", "undeliverable", "undeliverable_email", "bounce", "bounced", "spamtrap", "do_not_mail"}
_BLOCKING_FLAGS = {
    "invalid", "undeliverable", "bounce", "bounced", "spamtrap", "disposable",
    "do_not_contact", "do_not_mail", "opted_out", "suppressed", "complaint",
}


def _tokens(value) -> set[str]:
    if isinstance(value, str):
        values = re.split(r"[,;|\s]+", value.strip().lower())
    elif isinstance(value, list):
        values = [str(item).strip().lower() for item in value]
    elif isinstance(value, dict):
        values = [str(key).strip().lower() for key, enabled in value.items() if enabled]
    else:
        values = []
    return {re.sub(r"[^a-z0-9_]+", "_", item).strip("_") for item in values if item}


def lead_eligibility_reason(lead: dict, mode: str = "advisory") -> str | None:
    """Return a hold reason only for explicit risks; advisory mode tolerates missing verification."""
    result = str(lead.get("verification_result") or "").strip().lower()
    normalized_result = re.sub(r"[^a-z0-9_]+", "_", result).strip("_")
    if normalized_result in _INVALID_RESULTS:
        return "Email verification marks this address invalid or unsafe."
    flags = _tokens(lead.get("quality_flags"))
    matched = flags & _BLOCKING_FLAGS
    if matched:
        return "Lead quality flags require review: " + ", ".join(sorted(matched)) + "."
    if mode == "strict" and normalized_result not in _VALID_RESULTS:
        return "Email verification is missing or not marked valid; verify the address before outreach."
    return None
