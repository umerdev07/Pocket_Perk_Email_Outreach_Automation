"""Authenticated, durable inbound webhooks from Instantly."""
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.webhook_db import enqueue_instantly_webhook

router = APIRouter(prefix="/api/webhooks", tags=["provider webhooks"])
MAX_BODY_BYTES = 1_000_000


@router.post("/instantly", status_code=202)
async def receive_instantly_webhook(request: Request, settings: Settings = Depends(get_settings)):
    secret = settings.instantly_webhook_secret
    supplied = request.headers.get("x-pocketperks-webhook-secret", "")
    if not secret or not hmac.compare_digest(supplied, secret):
        raise HTTPException(status_code=401, detail="Webhook authentication failed.")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload is empty or too large.")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload is empty or too large.")
    body = bytes(chunks)
    if not body:
        raise HTTPException(status_code=413, detail="Webhook payload is empty or too large.")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Webhook payload must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")
    digest = hashlib.sha256(body).hexdigest()
    created = await enqueue_instantly_webhook(settings, digest, payload)
    return {"accepted": True, "duplicate": not created}
