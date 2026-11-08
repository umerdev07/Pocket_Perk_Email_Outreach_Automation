"""Supabase persistence for replies imported from Instantly Unibox."""
from datetime import UTC, datetime
import re

import httpx
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
from app.suppression_db import get_suppressed_emails, normalize_email, save_suppressions

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


def _base(settings: Settings) -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for lead replies.")
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(settings: Settings, prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _rows(response: httpx.Response, message: str) -> list[dict]:
    if not response.is_success:
        raise HTTPException(status_code=503, detail=message)
    try:
        result = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=message) from exc
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise HTTPException(status_code=503, detail=message)
    return result


async def list_replies(settings: Settings, *, limit: int = 100, offset: int = 0) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/replies", params={
            "select": "id,lead_id,campaign_id,from_email,sender_account_email,subject,body,instantly_id,received_at,status,created_at,leads(business_name,email),campaigns(name)",
            "order": "received_at.desc", "limit": limit, "offset": offset,
        }, headers=_headers(settings))
    rows = _rows(response, "Could not load lead replies.")
    # Reconcile clear opt-outs in already-saved replies too. The reply sync
    # cursor may have advanced before opt-out detection was deployed.
    existing_opt_outs = []
    for row in rows:
        lead = row.get("leads") if isinstance(row.get("leads"), dict) else {}
        address = normalize_email(lead.get("email") or row.get("from_email"))
        body = row.get("body") if isinstance(row.get("body"), str) else ""
        if address and _explicit_opt_out(body):
            existing_opt_outs.append({
                "email": address, "source": "reply_auto",
                "reason": "Recipient explicitly requested no further email.",
                "lead_id": row.get("lead_id"), "campaign_id": row.get("campaign_id"),
            })
    await save_suppressions(settings, existing_opt_outs)
    addresses = {str((row.get("leads") or {}).get("email") or row.get("from_email") or "") for row in rows}
    suppressed = await get_suppressed_emails(settings, addresses)
    for row in rows:
        lead = row.get("leads") if isinstance(row.get("leads"), dict) else {}
        address = normalize_email(lead.get("email") or row.get("from_email"))
        row["opted_out"] = address in suppressed if address else False
    return rows


async def save_received_replies(settings: Settings, emails: list[dict]) -> int:
    """Save only messages tied to our imported leads or mapped campaigns."""
    if not emails:
        return 0
    lead_emails = sorted({str(item.get("lead") or item.get("from_address_email") or "").strip().lower()
                          for item in emails
                          if isinstance(item.get("lead") or item.get("from_address_email"), str)
                          and (item.get("lead") or item.get("from_address_email")).strip()})
    campaign_ids = sorted({str(item.get("campaign_id")) for item in emails if item.get("campaign_id")})
    base, headers = _base(settings), _headers(settings)
    leads_by_email: dict[str, dict] = {}
    campaign_by_provider_id: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=25) as client:
        for offset in range(0, len(lead_emails), 100):
            chunk = lead_emails[offset:offset + 100]
            rows = _rows(await client.get(f"{base}/leads", params={
                "select": "id,email", "email": f"in.({','.join(chunk)})", "limit": len(chunk),
            }, headers=headers), "Could not match incoming replies to imported leads.")
            leads_by_email.update({str(row["email"]).lower(): row for row in rows if row.get("email")})
        for offset in range(0, len(campaign_ids), 100):
            chunk = campaign_ids[offset:offset + 100]
            rows = _rows(await client.get(f"{base}/instantly_campaign_mappings", params={
                "select": "instantly_campaign_id,campaign_runs(campaign_id)",
                "instantly_campaign_id": f"in.({','.join(chunk)})", "limit": len(chunk),
            }, headers=headers), "Could not match incoming replies to PocketPerks campaigns.")
            for row in rows:
                relation = row.get("campaign_runs")
                if isinstance(relation, dict) and row.get("instantly_campaign_id") and relation.get("campaign_id"):
                    campaign_by_provider_id[str(row["instantly_campaign_id"])] = str(relation["campaign_id"])

        pending = []
        opt_out_records = []
        for item in emails:
            provider_id = item.get("id")
            sender = item.get("from_address_email")
            lead_email = str(item.get("lead") or sender or "").strip().lower()
            lead = leads_by_email.get(lead_email)
            if not isinstance(provider_id, str) or not provider_id or not isinstance(sender, str) or not sender.strip():
                continue
            campaign_id = campaign_by_provider_id.get(str(item.get("campaign_id") or ""))
            # Keep genuine inbound messages even when they cannot be mapped to
            # an imported lead or known campaign; the inbox must not lose them.
            body_obj = item.get("body") if isinstance(item.get("body"), dict) else {}
            body = body_obj.get("text") or item.get("content_preview") or "(Reply body unavailable in provider response.)"
            if not isinstance(body, str):
                body = str(body)
            received_at = item.get("timestamp_email") or item.get("timestamp_created")
            if not isinstance(received_at, str):
                received_at = datetime.now(UTC).isoformat()
            pending.append({
                "lead_id": lead.get("id") if lead else None,
                "campaign_id": campaign_id,
                "from_email": sender.strip().lower(),
                "sender_account_email": _reply_mailbox_address(item),
                "subject": str(item.get("subject") or "(no subject)")[:500],
                "body": body[:20000],
                "instantly_id": provider_id,
                "received_at": received_at,
                "status": "new" if item.get("is_unread") else "read",
            })
            normalized_recipient = normalize_email((lead or {}).get("email") or lead_email)
            if normalized_recipient and _explicit_opt_out(body):
                opt_out_records.append({
                    "email": normalized_recipient, "source": "reply_auto",
                    "reason": "Recipient explicitly requested no further email.",
                    "lead_id": lead.get("id") if lead else None, "campaign_id": campaign_id,
                })
        # Persist opt-outs before advancing reply sync so failures are retried.
        await save_suppressions(settings, opt_out_records)
        inserted = 0
        for offset in range(0, len(pending), 100):
            chunk = pending[offset:offset + 100]
            response = await client.post(f"{base}/replies", params={"on_conflict": "instantly_id"},
                                         headers=_headers(settings, "resolution=ignore-duplicates,return=representation"),
                                         json=chunk)
            inserted += len(_rows(response, "Could not save replies to the PocketPerks workspace."))
        # Earlier syncs may have inserted the same reply before sender mailbox
        # metadata was stored. Backfill only the nullable field, preserving local
        # read/opt-out state and the original message contents.
        mailbox_groups: dict[str, list[str]] = {}
        for row in pending:
            if row.get("sender_account_email"):
                mailbox_groups.setdefault(row["sender_account_email"], []).append(row["instantly_id"])
        for mailbox, provider_ids in mailbox_groups.items():
            for offset in range(0, len(provider_ids), 100):
                chunk = provider_ids[offset:offset + 100]
                response = await client.patch(f"{base}/replies", params={
                    "instantly_id": f"in.({','.join(chunk)})", "sender_account_email": "is.null",
                }, headers=_headers(settings, "return=minimal"), json={"sender_account_email": mailbox})
                if not response.is_success:
                    raise HTTPException(status_code=503, detail="Could not save the original sending mailbox for replies.")
    return inserted


async def mark_reply_read(settings: Settings, reply_id: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(f"{_base(settings)}/replies", params={"id": f"eq.{reply_id}"},
                                      headers=_headers(settings, "return=representation"), json={"status": "read"})
    rows = _rows(response, "Could not update this reply.")
    if not rows:
        raise HTTPException(status_code=404, detail="Reply not found.")
    return rows[0]


async def send_reply(settings: Settings, reply_id: str, body: str) -> dict:
    """Send only a reply tied to a saved inbound Instantly email."""
    text = body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Reply text cannot be empty.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/replies", params={
            "select": "id,instantly_id,sender_account_email,subject", "id": f"eq.{reply_id}", "limit": "1",
        }, headers=_headers(settings))
        rows = _rows(response, "Could not load the reply before sending.")
    if not rows:
        raise HTTPException(status_code=404, detail="Reply not found.")
    row = rows[0]
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
        mailbox_filter = (f"eq.{row['sender_account_email']}" if row.get("sender_account_email")
                          else "is.null")
        async with httpx.AsyncClient(timeout=20) as client:
            saved = await client.patch(f"{_base(settings)}/replies", params={
                "id": f"eq.{reply_id}", "sender_account_email": mailbox_filter,
            }, headers=_headers(settings, "return=minimal"), json={"sender_account_email": sender_account})
        if not saved.is_success:
            raise HTTPException(status_code=503, detail="The sending mailbox was found, but could not be saved. Apply migration 029 and retry.")
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


async def delete_reply(settings: Settings, reply_id: str) -> dict:
    """Delete an Instantly Unibox email first, then remove its PocketPerks copy."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/replies", params={
            "select": "id,instantly_id", "id": f"eq.{reply_id}", "limit": "1",
        }, headers=_headers(settings))
        rows = _rows(response, "Could not load this reply before deletion.")
        if not rows:
            raise HTTPException(status_code=404, detail="Reply not found.")
        provider_id = rows[0].get("instantly_id")
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

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.delete(f"{_base(settings)}/replies", params={"id": f"eq.{reply_id}"},
                                       headers=_headers(settings, "return=representation"))
    deleted = _rows(response, "Instantly deleted the email, but PocketPerks could not remove its local reply. Refresh and retry.")
    if not deleted:
        # The provider deletion succeeded. Treat concurrent local deletion as an idempotent success.
        return {"reply_id": reply_id, "deleted": True}
    return {"reply_id": reply_id, "deleted": True}


async def mark_reply_opt_out(settings: Settings, reply_id: str, actor_user_id: str) -> dict:
    """Manually confirm an ambiguous reply as an opt-out and suppress globally."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/replies", params={
            "select": "id,lead_id,campaign_id,from_email,leads(email)", "id": f"eq.{reply_id}", "limit": "1",
        }, headers=_headers(settings))
        rows = _rows(response, "Could not load this reply to record its opt-out.")
    if not rows:
        raise HTTPException(status_code=404, detail="Reply not found.")
    row = rows[0]
    lead = row.get("leads") if isinstance(row.get("leads"), dict) else {}
    email = normalize_email(lead.get("email") or row.get("from_email"))
    if not email:
        raise HTTPException(status_code=422, detail="This reply does not have a valid recipient address to suppress.")
    await save_suppressions(settings, [{
        "email": email, "source": "reply_manual", "reason": "Opt-out confirmed by a workspace reviewer.",
        "lead_id": row.get("lead_id"), "campaign_id": row.get("campaign_id"),
        "actor_user_id": actor_user_id,
    }])
    return {"reply_id": reply_id, "email": email, "opted_out": True}


async def get_reply_sync_state(settings: Settings) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_base(settings)}/instantly_reply_sync_state", params={
            "select": "last_checked_at,sync_from,starting_after", "id": "eq.true", "limit": "1",
        }, headers=_headers(settings))
    rows = _rows(response, "Could not load the Instantly reply sync cursor. Apply migration 017 and retry.")
    if not rows:
        raise HTTPException(status_code=503, detail="Instantly reply sync cursor is missing. Apply migration 017.")
    return rows[0]


async def update_reply_sync_state(settings: Settings, *, last_checked_at: str | None = None,
                                  sync_from: str | None = None,
                                  starting_after: str | None = None) -> None:
    values = {"sync_from": sync_from, "starting_after": starting_after,
              "updated_at": datetime.now(UTC).isoformat()}
    if last_checked_at is not None:
        values["last_checked_at"] = last_checked_at
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(f"{_base(settings)}/instantly_reply_sync_state",
                                      params={"id": "eq.true"},
                                      headers=_headers(settings, "return=representation"), json=values)
    rows = _rows(response, "Could not update the Instantly reply sync cursor.")
    if not rows:
        raise HTTPException(status_code=503, detail="Instantly reply sync cursor was not updated.")
