"""Assign user-selected Instantly accounts and launch only after safe sync."""
from datetime import UTC, datetime

from fastapi import HTTPException

from app.campaign_runs_db import get_campaign_run_instantly_status
from app.config import Settings
from app.instantly_sync_db import (get_campaign_mapping_for_run, load_sync_run_context,
                                   update_campaign_mapping)
from app.integrations.instantly_client import (InstantlyError, activate_campaign,
                                               list_sending_accounts,
                                               set_campaign_sending_accounts)
from app.instantly_sync_service import _aware_datetime


def is_sending_account_ready(account: dict) -> bool:
    status = str(account.get("status", "")).strip().lower()
    return status in {"1", "active", "connected"} and not account.get("setup_pending")


async def assign_senders_and_continue(settings: Settings, run_id: str, emails: list[str]) -> dict:
    selected = list(dict.fromkeys(email.strip().lower() for email in emails if isinstance(email, str)))
    if not selected:
        raise HTTPException(status_code=422, detail="Select at least one sending inbox.")
    mapping = await get_campaign_mapping_for_run(settings, run_id)
    if not mapping or not mapping.get("instantly_campaign_id"):
        raise HTTPException(status_code=409, detail="The Instantly campaign is not ready for sender selection yet.")
    if mapping.get("status") not in {"leads_synced", "scheduled", "active"}:
        raise HTTPException(status_code=409, detail="Wait until the lead sync has finished before selecting senders.")

    try:
        accounts = await list_sending_accounts(settings)
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    available = {str(account.get("email", "")).strip().lower(): account
                 for account in accounts if is_sending_account_ready(account)}
    invalid = [email for email in selected if email not in available]
    if invalid:
        raise HTTPException(status_code=422, detail="Choose only active, connected Instantly inboxes.")

    sync_status = await get_campaign_run_instantly_status(settings, run_id)
    if sync_status["synced_leads"] < 1:
        raise HTTPException(status_code=409, detail="At least one lead must sync successfully before sending can continue.")
    if sync_status["pending_leads"] or sync_status["failed_leads"] or sync_status.get("unknown_leads", 0):
        raise HTTPException(status_code=409, detail="Resolve all pending or uncertain lead syncs before assigning senders.")

    provider_id = str(mapping["instantly_campaign_id"])
    try:
        await set_campaign_sending_accounts(provider_id, selected, settings)
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if mapping.get("status") == "active":
        await update_campaign_mapping(settings, str(mapping["id"]), {
            "sender_account_emails": selected, "error_detail": None,
        })
        return {"run_id": run_id, "status": "active", "sender_account_emails": selected,
                "message": "Sending inboxes updated on the active Instantly campaign."}

    context = await load_sync_run_context(settings, run_id)
    campaign = context["campaign"]
    if not settings.instantly_auto_launch or settings.send_mode != "live":
        await update_campaign_mapping(settings, str(mapping["id"]), {
            "sender_account_emails": selected, "status": "leads_synced", "error_detail": None,
        })
        return {"run_id": run_id, "status": "leads_synced", "sender_account_emails": selected,
                "message": "Sending inboxes assigned. Automatic launch is disabled by configuration."}

    try:
        start_at = _aware_datetime(campaign.get("start_at"), "start")
        end_at = _aware_datetime(campaign.get("end_at"), "end")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if end_at <= start_at or end_at <= datetime.now(UTC):
        raise HTTPException(status_code=409, detail="The campaign sending window has ended or is invalid.")
    if start_at > datetime.now(UTC):
        await update_campaign_mapping(settings, str(mapping["id"]), {
            "sender_account_emails": selected, "status": "scheduled", "error_detail": None,
        })
        return {"run_id": run_id, "status": "scheduled", "sender_account_emails": selected,
                "message": "Sending inboxes assigned. Instantly will activate the campaign at its start time."}

    await update_campaign_mapping(settings, str(mapping["id"]), {
        "sender_account_emails": selected, "status": "launching",
        "error_detail": "Sender selection saved; activation is being confirmed.",
    })
    try:
        await activate_campaign(provider_id, settings)
    except InstantlyError as exc:
        outcome_unknown = getattr(exc, "outcome_unknown", False)
        await update_campaign_mapping(settings, str(mapping["id"]), {
            "status": "launching" if outcome_unknown else "leads_synced",
            "error_detail": "Instantly activation outcome is unknown; check campaign status before retrying."
            if outcome_unknown else f"Instantly campaign activation failed: {str(exc)}"[:240],
        })
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await update_campaign_mapping(settings, str(mapping["id"]), {
        "sender_account_emails": selected, "status": "active", "error_detail": None,
    })
    return {"run_id": run_id, "status": "active", "sender_account_emails": selected,
            "message": "Sending inboxes assigned and Instantly confirmed campaign activation."}
