"""PostgreSQL-backed reply operations and Instantly thread actions."""
from datetime import UTC, datetime
import re

from fastapi import HTTPException

from app.config import Settings
from app.integrations.instantly_client import (
    AuthenticationError,
    InstantlyError,
    PlanAccessError,
    delete_email as delete_instantly_email,
    get_email as get_instantly_email,
    reply_to_email as reply_to_instantly_email,
)
from app.suppression_db import normalize_email, save_suppressions

_OPT_OUT_RE = re.compile(
    r"\b(?:no\s+thanks|unsubscribe(?:\s+me)?|opt\s+me\s+out|remove\s+me\s+from\s+(?:your\s+)?(?:list|mailing\s+list)|"
    r"take\s+me\s+off\s+(?:your\s+)?(?:list|mailing\s+list)|do\s+not\s+(?:email|contact)\s+me|"
    r"don't\s+(?:email|contact)\s+me|i\s+don't\s+want\s+(?:any\s+)?(?:more\s+)?emails?|"
    r"no\s+more\s+emails|stop\s+(?:emailing|contacting|sending\s+me\s+emails?))\b",
    re.IGNORECASE,
)
_QUOTED_REPLY_MARKER = re.compile(
    r"^\s*(?:On .{3,220}wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s+.+|>|_{5,}\s*$)",
    re.IGNORECASE,
)
_INLINE_QUOTED_REPLY_MARKER = re.compile(r"\bOn .{3,220}?wrote:\s*", re.IGNORECASE)


def _explicit_opt_out(body: str) -> bool:
    """Classify only clear requests in the new reply, never quoted campaign copy."""
    new_lines = []
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _QUOTED_REPLY_MARKER.match(line):
            break
        inline_quote = _INLINE_QUOTED_REPLY_MARKER.search(line)
        if inline_quote:
            line = line[:inline_quote.start()]
            new_lines.append(line)
            break
        new_lines.append(line)
    new_text = " ".join(new_lines).strip()
    # Mentioning an unsubscribe policy or process is not itself a request to opt out.
    # Keep this narrow so an explicit "unsubscribe me" still wins below.
    discusses_policy = re.search(
        r"\b(?:review|read|check|understand|explain|discuss)\b.{0,60}"
        r"\b(?:unsubscribe|opt\s*out)\b.{0,40}\b(?:policy|process|option|link)\b",
        new_text, re.IGNORECASE,
    )
    explicit_request = re.search(
        r"\b(?:unsubscribe\s+me|opt\s+me\s+out|remove\s+me\s+from|"
        r"take\s+me\s+off|do\s+not\s+(?:email|contact)\s+me|"
        r"don't\s+(?:email|contact)\s+me|no\s+more\s+emails?|"
        r"stop\s+(?:emailing|contacting|sending\s+me\s+emails?))\b",
        new_text, re.IGNORECASE,
    )
    if discusses_policy and not explicit_request:
        return False
    return bool(new_text and (_OPT_OUT_RE.search(new_text) or re.fullmatch(r"stop[.!\s]*", new_text, re.I)))


def _reply_mailbox_address(item: dict) -> str | None:
    """Resolve Instantly's mailbox reference to the actual account email."""
    account = normalize_email(item.get("eaccount"))
    if account:
        return account
    recipients = item.get("to_address_email_list")
    if isinstance(recipients, str):
        candidates = recipients.split(",")
    elif isinstance(recipients, list):
        candidates = [value.get("email") if isinstance(value, dict) else value for value in recipients]
    else:
        candidates = []
    return next((email for value in candidates if (email := normalize_email(value))), None)


async def list_replies(settings: Settings, *, limit: int = 100, offset: int = 0, pool=None) -> list[dict]:
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    from app.replies_postgres import list_replies as pg_list
    return await pg_list(pool, settings, limit=limit, offset=offset)


async def save_received_replies(settings: Settings, emails: list[dict], pool=None) -> int:
    """Save only messages tied to our imported leads or mapped campaigns."""
    if not emails:
        return 0
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    from app.replies_postgres import save_received_replies as pg_save
    return await pg_save(pool, emails)


async def mark_reply_read(settings: Settings, reply_id: str, pool=None) -> dict:
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    from app.replies_postgres import mark_reply_read as pg_mark
    return await pg_mark(pool, reply_id)


async def send_reply(settings: Settings, reply_id: str, body: str, pool=None) -> dict:
    """Send only a reply tied to a saved inbound Instantly email."""
    text = body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Reply text cannot be empty.")
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    from app.replies_postgres import reply_context, set_reply_mailbox
    row = await reply_context(pool, reply_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reply not found.")
    row = dict(row)
    provider_id = row.get("instantly_id")
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise HTTPException(status_code=409, detail="This reply has no saved Instantly email ID, so it cannot be sent as a thread reply.")
    sender_account = normalize_email(row.get("sender_account_email"))
    if not sender_account:
        # Older reply rows may predate migration 029 or fall outside the normal
        # polling cursor. Resolve this exact provider email on demand, then save
        # the mailbox locally so later replies do not need another lookup.
        try:
            provider_email = await get_instantly_email(provider_id, settings=settings)
        except AuthenticationError as exc:
            raise HTTPException(status_code=503, detail="Looking up this thread needs Instantly emails:read; sending also needs emails:create.") from exc
        except PlanAccessError as exc:
            raise HTTPException(status_code=503, detail="The Instantly plan does not allow looking up this Unibox thread.") from exc
        except InstantlyError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        sender_account = _reply_mailbox_address(provider_email)
        if not sender_account:
            raise HTTPException(status_code=409, detail="Instantly did not return the sending mailbox for this thread. Check the original mailbox is still connected.")
        await set_reply_mailbox(pool, reply_id, sender_account)
    try:
        result = await reply_to_instantly_email(provider_id, sender_account,
                                                str(row.get("subject") or ""), text, settings=settings)
    except AuthenticationError as exc:
        raise HTTPException(status_code=503, detail="Instantly reply sending needs an API key with the emails:create scope.") from exc
    except PlanAccessError as exc:
        raise HTTPException(status_code=503, detail="The Instantly plan does not allow sending replies through the API.") from exc
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"reply_id": reply_id, "sent": True, "provider_email_id": result.get("id")}


async def delete_reply(settings: Settings, reply_id: str, pool=None) -> dict:
    """Delete an Instantly Unibox email first, then remove its PocketPerks copy."""
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    from app.replies_postgres import delete_reply as pg_delete, reply_context
    row = await reply_context(pool, reply_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reply not found.")
    provider_id = row.get("instantly_id")
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise HTTPException(status_code=409, detail="This reply has no Instantly email ID, so it was kept in both views.")

    try:
        await delete_instantly_email(provider_id, settings=settings)
    except AuthenticationError as exc:
        raise HTTPException(status_code=503, detail=(
            "Instantly deletion needs an API key with the emails:delete scope. Create a new key with that scope and update the backend key."
        )) from exc
    except PlanAccessError as exc:
        raise HTTPException(status_code=503, detail="The Instantly plan does not allow deleting this Unibox email.") from exc
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await pg_delete(pool, reply_id)


async def mark_reply_opt_out(settings: Settings, reply_id: str, actor_user_id: str, pool=None) -> dict:
    """Manually confirm an ambiguous reply as an opt-out and suppress globally."""
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    from app.replies_postgres import opt_out_context
    saved = await opt_out_context(pool, reply_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Reply not found.")
    row = dict(saved)
    email = normalize_email(row.get("email") or row.get("from_email"))
    if not email:
        raise HTTPException(status_code=422, detail="This reply does not have a valid recipient address to suppress.")
    await save_suppressions(settings, [{
        "email": email, "source": "reply_manual", "reason": "Opt-out confirmed by a workspace reviewer.",
        "lead_id": row.get("lead_id"), "campaign_id": row.get("campaign_id"),
        "actor_user_id": actor_user_id,
    }],pool=pool)
    return {"reply_id": reply_id, "email": email, "opted_out": True}


async def get_reply_sync_state(settings: Settings, pool=None) -> dict:
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    from app.replies_postgres import get_sync_state
    return await get_sync_state(pool)


async def update_reply_sync_state(settings: Settings, *, last_checked_at: str | None = None,
                                  sync_from: str | None = None,
                                  starting_after: str | None = None, pool=None) -> None:
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable.")
    from app.replies_postgres import update_sync_state
    return await update_sync_state(pool, last_checked_at=last_checked_at,
                                   sync_from=sync_from, starting_after=starting_after)
