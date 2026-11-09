"""Human approval queue and review actions."""
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.approvals_db import (get_email_review_context, list_pending_approvals,
                              list_review_history, update_review_email)
from app.config import Settings, get_settings
from app.services.risk_service import PLACEHOLDER_RE, assess_personalized_email

router = APIRouter(tags=["human approvals"])


class EmailEdit(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=20000)

    @model_validator(mode="after")
    def validate_edit(self):
        if self.subject is None and self.body is None:
            raise ValueError("Provide a subject or body to edit.")
        if self.subject is not None and not self.subject.strip():
            raise ValueError("Subject cannot be empty.")
        if self.body is not None and not self.body.strip():
            raise ValueError("Body cannot be empty.")
        return self


@router.get("/api/approvals")
async def get_approval_queue(limit: int = Query(default=50, ge=1, le=100),
                             offset: int = Query(default=0, ge=0),
                             settings: Settings = Depends(get_settings)):
    return await list_pending_approvals(settings, limit=limit, offset=offset)


@router.get("/api/approvals/history")
async def get_approval_history(limit: int = Query(default=50, ge=1, le=100),
                               offset: int = Query(default=0, ge=0),
                               search: str = Query(default="", max_length=80),
                               campaign: str = Query(default="", max_length=160),
                               action: str = Query(default="", pattern="^(|risk_assessed|human_approved|human_rejected|email_edited|auto_queued|sent_to_review|risk_rechecked|status_changed)$"),
                               status: str = Query(default="", pattern="^(|generated|pending_review|approved|queued|sent|sent_test|failed|rejected)$"),
                               risk_level: str = Query(default="", pattern="^(|LOW|MEDIUM|HIGH)$"),
                               days: int = Query(default=30, ge=0, le=365),
                               settings: Settings = Depends(get_settings)):
    return await list_review_history(settings, limit=limit, offset=offset, search=search,
                                     campaign=campaign, action=action, status=status,
                                     risk_level=risk_level, days=days)


@router.patch("/api/emails/{email_id}")
async def edit_personalized_email(email_id: UUID, payload: EmailEdit,
                                  settings: Settings = Depends(get_settings)):
    context = await get_email_review_context(settings, str(email_id))
    if context is None:
        raise HTTPException(status_code=404, detail="Personalized email not found.")
    email = context["email"]
    old_status = email["status"]
    if old_status not in {"pending_review", "approved", "queued"}:
        raise HTTPException(status_code=409, detail="This email cannot be edited in its current status.")

    edits = payload.model_dump(exclude_none=True)
    subject = edits.get("subject", email["subject"]).strip()
    body = edits.get("body", email["body"]).strip()
    risk = await assess_personalized_email(subject, body, draft=context["draft"], lead=context["lead"],
                                           settings=settings, campaign=context.get("campaign"),
                                           step=context.get("step"))
    manual_trigger = context.get("step", {}).get("trigger_type") == "after_conversation"
    status = ("approved" if manual_trigger else "queued") if risk["risk_level"] == "LOW" else "pending_review"
    return await update_review_email(settings, str(email_id), old_status, {
        "subject": subject, "body": body, "risk_level": risk["risk_level"],
        "risk_reason": f"Email was edited. Content-level spam check: {risk['risk_reason']}",
        "status": status, "approved_version_hash": None, "reviewed_at": None,
    })


@router.post("/api/emails/{email_id}/recheck")
async def recheck_personalized_email(email_id: UUID, settings: Settings = Depends(get_settings)):
    """Re-run current risk rules for a queued review item; LOW goes to queue, never to sent."""
    context = await get_email_review_context(settings, str(email_id))
    if context is None:
        raise HTTPException(status_code=404, detail="Personalized email not found.")
    email = context["email"]
    if email["status"] != "pending_review":
        raise HTTPException(status_code=409, detail="Only emails in the human approval queue can be rechecked.")
    risk = await assess_personalized_email(email["subject"], email["body"], draft=context["draft"],
                                           lead=context["lead"], settings=settings,
                                           campaign=context.get("campaign"), step=context.get("step"))
    manual_trigger = context.get("step", {}).get("trigger_type") == "after_conversation"
    status = ("approved" if manual_trigger else "queued") if risk["risk_level"] == "LOW" else "pending_review"
    updated = await update_review_email(settings, str(email_id), "pending_review", {
        "risk_level": risk["risk_level"], "risk_reason": risk["risk_reason"], "status": status,
    })
    return {**updated, "risk_level": risk["risk_level"], "risk_reason": risk["risk_reason"],
            "auto_queued": status == "queued", "ready_for_manual_trigger": status == "approved", "sent": False}


@router.post("/api/emails/{email_id}/approve")
async def approve_personalized_email(email_id: UUID, settings: Settings = Depends(get_settings)):
    context = await get_email_review_context(settings, str(email_id))
    if context is None:
        raise HTTPException(status_code=404, detail="Personalized email not found.")
    email = context["email"]
    if email["status"] != "pending_review":
        raise HTTPException(status_code=409, detail="Only emails in the human approval queue can be approved.")
    if PLACEHOLDER_RE.search(email["subject"] or "") or PLACEHOLDER_RE.search(email["body"] or ""):
        raise HTTPException(
            status_code=409,
            detail="This email still contains a placeholder. Edit it (for example, replace [Your Name]) before approval.",
        )
    content_hash = hashlib.sha256(f"{email['subject']}\0{email['body']}".encode("utf-8")).hexdigest()
    return await update_review_email(settings, str(email_id), "pending_review", {
        # Scheduled approvals enter the Instantly sequence. Conversation steps
        # remain approved until the user explicitly confirms the conversation.
        "status": "approved" if context.get("step", {}).get("trigger_type") == "after_conversation" else "queued",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "approved_version_hash": content_hash,
    })


@router.post("/api/emails/{email_id}/reject")
async def reject_personalized_email(email_id: UUID, settings: Settings = Depends(get_settings)):
    context = await get_email_review_context(settings, str(email_id))
    if context is None:
        raise HTTPException(status_code=404, detail="Personalized email not found.")
    if context["email"]["status"] != "pending_review":
        raise HTTPException(status_code=409, detail="Only emails in the human approval queue can be rejected.")
    return await update_review_email(settings, str(email_id), "pending_review", {
        "status": "rejected", "reviewed_at": datetime.now(UTC).isoformat(),
        "approved_version_hash": None,
    })
