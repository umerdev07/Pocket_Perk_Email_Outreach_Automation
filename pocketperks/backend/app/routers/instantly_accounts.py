from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.integrations.instantly_client import InstantlyError, list_sending_accounts
from app.instantly_sender_service import is_sending_account_ready

router = APIRouter(prefix="/api/instantly", tags=["Instantly"])


@router.get("/accounts")
async def instantly_accounts(settings: Settings = Depends(get_settings)):
    try:
        accounts = await list_sending_accounts(settings)
    except InstantlyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [{**account, "sending_ready": is_sending_account_ready(account)} for account in accounts]
