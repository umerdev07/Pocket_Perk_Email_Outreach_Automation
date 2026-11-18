from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app import team_postgres

router = APIRouter(prefix="/api/admin/users", tags=["workspace users"])
Role = Literal["admin", "operator", "reviewer"]


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Role = "operator"


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


def _pool(request: Request):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL user management is not configured.")
    return pool


@router.get("")
async def get_users(request: Request):
    return {"items": await team_postgres.list_users(_pool(request))}


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_user(payload: UserCreate, request: Request):
    email = payload.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    actor = request.state.workspace_user
    return await team_postgres.invite_user(_pool(request), request.app.state.settings,
                                           email=email, role=payload.role, actor_id=actor["id"])


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invitation(invitation_id: UUID, request: Request):
    await team_postgres.cancel_invitation(_pool(request), str(invitation_id))
    return None


@router.patch("/{user_id}")
async def edit_user(user_id: UUID, payload: UserUpdate, request: Request):
    if (payload.role is None) == (payload.is_active is None):
        raise HTTPException(status_code=422, detail="Choose one change at a time.")
    actor = request.state.workspace_user
    if payload.role is not None:
        return await team_postgres.request_role_change(
            _pool(request), request.app.state.settings, user_id=str(user_id),
            actor_id=actor["id"], role=payload.role)
    return await team_postgres.update_user(_pool(request), user_id=str(user_id),
                                           actor_id=actor["id"], is_active=payload.is_active)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, request: Request):
    actor = request.state.workspace_user
    await team_postgres.remove_user(_pool(request), user_id=str(user_id), actor_id=actor["id"])
    return None


@router.delete("/{user_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_user(user_id: UUID, request: Request):
    actor = request.state.workspace_user
    await team_postgres.permanently_delete_user(
        _pool(request), user_id=str(user_id), actor_id=actor["id"])
    return None
