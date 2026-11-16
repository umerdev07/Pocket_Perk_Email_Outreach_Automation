"""Validate and check alignment for human-written campaign email contexts."""
import re

CONTEXT_HELP = (
    "Write one clear direction for this email and tie it to the campaign goal or PocketPerks. "
    "Say what to explain, ask, or follow up on. Example: ‘Introduce the phone-based loyalty card and invite them to a demo.’ "
    "Avoid random text or unrelated topics."
)

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]*")
_STOP_WORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "give", "in", "into",
    "is", "it", "its", "of", "on", "or", "our", "the", "their", "them", "this", "to",
    "we", "with", "you", "your", "email", "step", "send", "write", "make", "keep",
}
_STAGE_ONLY = {
    "intro", "introduction", "first email", "follow up", "follow-up", "followup",
    "send a polite follow-up", "polite follow-up", "reminder", "check in", "check-in", "thank you", "closing", "close the loop",
}
_RELEVANT_TERMS = {
    "pocketperks", "loyalty", "stamp", "stamps", "card", "cards", "digital", "phone",
    "mobile", "customer", "customers", "client", "clients", "regular", "reward", "rewards",
    "cafe", "cafes", "barber", "barbers", "restaurant", "restaurants", "business",
    "demo", "meeting", "call", "reply", "interested", "introduce", "introduction", "explain",
    "describe", "show", "mention", "ask", "invite", "follow", "followup", "remind", "reminder",
    "thank", "polite", "friendly", "brief", "concise", "simple", "setup", "qr", "monthly",
    "subscription", "fee", "price", "pricing", "cost", "free", "paper", "marketing", "appointment",
}
_CONCEPTS = {
    "loyalty": {"loyalty", "stamp", "stamps", "reward", "rewards"},
    "card": {"card", "cards", "loyalty", "stamp", "stamps"},
    "digital": {"digital", "phone", "mobile", "qr"},
    "audience": {"cafe", "cafes", "café", "barber", "barbers", "restaurant", "restaurants", "customer", "customers", "client", "clients", "regular"},
    "demo": {"demo", "meeting", "call", "show"},
    "followup": {"follow", "followup", "reminder", "check", "checking"},
    "price": {"monthly", "subscription", "fee", "price", "pricing", "cost", "free"},
    "action": {"introduce", "introduction", "explain", "describe", "mention", "ask", "invite", "reply", "interested", "thank"},
    "tone": {"polite", "friendly", "brief", "concise", "simple"},
    "setup": {"setup", "qr", "reward", "rewards", "materials"},
}


def _tokens(value: object) -> list[str]:
    return [word.lower().replace("'", "") for word in _WORD_RE.findall(str(value or ""))]


def _stage_only(context: str) -> bool:
    normalized = " ".join(_tokens(context))
    return normalized in {" ".join(_tokens(item)) for item in _STAGE_ONLY}


def context_issue(context: str, *, campaign_name: str = "", instructions: str = "") -> str | None:
    """Return a clear validation message for empty, random or off-topic context."""
    text = str(context or "").strip()
    if not text:
        return "Add a relevant direction for this email step. " + CONTEXT_HELP
    if len(text) > 600:
        return "Keep the email context under 600 characters and focused on one direction."
    if _stage_only(text):
        return None
    words = _tokens(text)
    content = [word for word in words if word not in _STOP_WORDS and len(word) > 1]
    if len(content) < 3:
        return CONTEXT_HELP
    alphabetic = [word for word in content if len(word) >= 4 and not word.isupper()]
    if alphabetic and sum(not any(char in "aeiouy" for char in word) for word in alphabetic) > len(alphabetic) // 2:
        return "This looks like random or unreadable text. " + CONTEXT_HELP
    campaign_terms = set(_tokens(campaign_name + " " + instructions)) - _STOP_WORDS
    if not (set(content) & (_RELEVANT_TERMS | campaign_terms)):
        return "Keep the context connected to the campaign topic or PocketPerks offer. " + CONTEXT_HELP
    return None


def context_alignment_issue(context: str, subject: str, body: str, *,
                             campaign_name: str = "", instructions: str = "") -> str | None:
    """Check that the generated email reflects at least half of its context ideas."""
    issue = context_issue(context, campaign_name=campaign_name, instructions=instructions)
    if issue or _stage_only(context):
        return issue
    terms = set(_tokens(context)) - _STOP_WORDS
    concepts = {name for name, aliases in _CONCEPTS.items() if terms & aliases}
    if not concepts:
        return None
    output = set(_tokens(subject + " " + body))
    covered = sum(bool(output & _CONCEPTS[name]) for name in concepts)
    required = max(1, (len(concepts) + 1) // 2)
    if covered < required:
        return "The email did not follow enough of this step's context. Rewrite it to clearly include the requested topic or action."
    return None
