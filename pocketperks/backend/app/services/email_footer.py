"""Canonical sender identity and opt-out footer for PocketPerks outreach."""
import re


_OLD_SYSTEM_OPT_OUT = re.compile(
    r"\n*(?:To stop future PocketPerks emails, reply STOP\.|If you would rather not hear from us, just reply ['\"]?no thanks['\"]?\.)\s*$",
    re.IGNORECASE,
)
_OLD_SYSTEM_SIGNATURE = re.compile(
    r"\n+(?:best|kind regards|regards),?\s*\n(?:the\s+)?pocketperks(?:\s+team)?\s*$",
    re.IGNORECASE,
)


def outreach_footer(settings) -> str:
    sender = str(getattr(settings, "outreach_sender_name", "") or "").strip() or "The PocketPerks Team"
    company = str(getattr(settings, "outreach_company_name", "") or "").strip() or "VenturesSky Ltd"
    website = str(getattr(settings, "outreach_website", "") or "").strip() or "https://pocketperks.co.uk"
    phone = str(getattr(settings, "outreach_phone", "") or "").strip()
    lines = ["Best,", sender, f"PocketPerks | {company}"]
    if phone:
        lines.append(phone)
    if website:
        lines.append(website)
    lines.extend(["", "If you would rather not hear from us, just reply 'no thanks'."])
    return "\n".join(lines)


def append_outreach_footer(body: str, settings) -> str:
    """Replace the previous app-owned STOP/sign-off footer, then append the canonical one."""
    clean = str(body or "").rstrip()
    clean = _OLD_SYSTEM_OPT_OUT.sub("", clean).rstrip()
    clean = _OLD_SYSTEM_SIGNATURE.sub("", clean).rstrip()
    footer = outreach_footer(settings)
    if clean.endswith(footer):
        return clean
    return f"{clean}\n\n{footer}" if clean else footer
