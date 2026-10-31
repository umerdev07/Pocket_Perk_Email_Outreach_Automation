"""Lightweight email-content spam risk assessment for personalized drafts.

Text checks cannot determine actual Gmail inbox placement. Sender reputation,
authentication, recipient engagement, and many provider-side signals matter too.
"""
import json
import re

from app.config import Settings
from app.integrations.openrouter_client import chat_completion_json

LEVELS = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
PLACEHOLDER_RE = re.compile(r"\{\{\s*[^}]+\}\}|\[[^\]]+\]")
SPAM_PATTERNS = {
    "deceptive urgency": re.compile(r"\b(?:act now|urgent|limited time|expires today|final notice)\b", re.I),
    "unrealistic promise or prize": re.compile(r"\b(?:guaranteed|risk[- ]free|you(?:'|’)ve won|claim your prize|free money)\b", re.I),
    "pressure to click": re.compile(r"\b(?:click here now|claim now|verify your account|confirm your details)\b", re.I),
    "financial bait": re.compile(r"\b(?:cash bonus|instant cash|debt relief|make money fast)\b", re.I),
}
ALL_CAPS_RE = re.compile(r"\b[A-Z]{3,}(?:\s+[A-Z]{3,}){2,}\b")
URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()]+", re.I)
FAKE_REPLY_RE = re.compile(r"^\s*(?:re|fwd?):\s*", re.I)


def _rule_assessment(subject: str, body: str, draft: dict, lead: dict,
                     campaign: dict | None = None, step: dict | None = None) -> tuple[str, list[str]]:
    """Check only message completeness and obvious content/format spam signals."""
    del draft, campaign, step  # Context matching and unsupported-claim policy are outside this check.
    reasons: list[str] = []
    if not subject.strip() or not body.strip() or PLACEHOLDER_RE.search(subject) or PLACEHOLDER_RE.search(body):
        return "HIGH", ["Subject or body is empty or still contains a placeholder."]
    if not (lead.get("email") or "").strip():
        return "HIGH", ["Recipient email is missing."]

    text = f"{subject}\n{body}"
    points = 0
    matched_patterns = [name for name, pattern in SPAM_PATTERNS.items() if pattern.search(text)]
    if matched_patterns:
        points += 2 * len(matched_patterns)
        reasons.append("Spam-like wording: " + ", ".join(matched_patterns) + ".")
    if ALL_CAPS_RE.search(text):
        points += 1
        reasons.append("Several consecutive words are written in all capitals.")
    if text.count("!") >= 3 or re.search(r"[!?]{2,}", text):
        points += 1
        reasons.append("Repeated exclamation or question marks create a high-pressure tone.")
    if len(subject) > 80:
        points += 1
        reasons.append("The subject is unusually long for outreach.")
    if len(body.split()) > 180:
        points += 1
        reasons.append("The message body is unusually long for outreach.")
    links = URL_RE.findall(body)
    if len(links) > 1:
        points += min(3, len(links))
        reasons.append(f"The body contains {len(links)} links.")
    if FAKE_REPLY_RE.match(subject):
        points += 2
        reasons.append("The subject looks like a reply or forward even though this is new outreach.")

    level = "HIGH" if points >= 6 else "MEDIUM" if points else "LOW"
    return level, reasons


def _valid_ai_result(value: object) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    level, reason = value.get("level"), value.get("reason")
    if not isinstance(level, str) or level.upper() not in LEVELS:
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return level.upper(), reason.strip()[:500]


async def assess_personalized_email(subject: str, body: str, *, draft: dict, lead: dict,
                                   settings: Settings, campaign: dict | None = None,
                                   step: dict | None = None) -> dict[str, str]:
    """Assess only obvious spam/content signals; never claim to predict Gmail placement."""
    rule_level, reasons = _rule_assessment(subject, body, draft, lead, campaign, step)
    if rule_level == "HIGH":
        return {"risk_level": "HIGH", "risk_reason": "; ".join(reasons)}

    messages = [
        {"role": "system", "content": (
            "Estimate whether the email content is likely to look spam-like to a mailbox spam filter, using only the subject and body. "
            "Look for deceptive urgency, unrealistic promises or prizes, financial bait, coercive requests to click or verify "
            "details, fake reply/forward subjects, excessive capitals or punctuation, multiple links, and unusually long copy. "
            "Do not judge campaign fit, location, whether the email should exist, or sender reputation. LOW means no meaningful "
            "content signal; MEDIUM means one or more plausible spam signals; HIGH means multiple or especially strong signals. "
            "Name the exact signal in the reason. Do not state that Gmail will definitely put it in Spam or Inbox: this is a "
            "content-only estimate and Gmail also evaluates authentication, domain reputation, recipient behavior, and other "
            "signals unavailable here. Return only JSON with level (LOW, MEDIUM, or HIGH) and a concise reason."
        )},
        {"role": "user", "content": json.dumps({"subject": subject, "body": body}, ensure_ascii=False)},
    ]
    try:
        ai_result = _valid_ai_result(await chat_completion_json(messages, settings))
    except Exception:
        ai_result = None

    # A provider outage should not send every otherwise clean message to manual
    # review. Keep the deterministic content scan as the fallback and log it.
    if ai_result is None:
        reason = "; ".join(reasons) if reasons else "No obvious content-level spam signals found."
        return {"risk_level": rule_level,
                "risk_reason": f"{reason} AI check unavailable; Gmail placement cannot be predicted from text."}

    ai_level, ai_reason = ai_result
    final_level = max((rule_level, ai_level), key=LEVELS.__getitem__)
    if ai_level != "LOW":
        reasons.append(f"Content spam-risk estimate: {ai_reason}")
    if not reasons:
        reasons.append("No obvious content-level spam signals found; Gmail placement is not guaranteed.")
    return {"risk_level": final_level, "risk_reason": "; ".join(reasons)[:1000]}
