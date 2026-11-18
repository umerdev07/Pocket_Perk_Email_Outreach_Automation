"""PostgreSQL operations for managing users in the shared PocketPerks workspace."""

from __future__ import annotations

import asyncpg
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import HTTPException

from app.auth_postgres import TEAM_CONFIRMATION_HOURS, _send_mail

ROLES = {"admin", "operator", "reviewer"}


async def list_users(pool) -> list[dict]:
    rows = await pool.fetch("""select u.id,u.email,u.is_active,u.access_removed_by_admin,u.created_at,u.last_login_at,m.role,
        (select t.new_role from public.workspace_role_change_tokens t where t.user_id=u.id
          and t.accepted_at is null and t.expires_at>now() limit 1) as pending_role
        from public.users u join public.workspace_members m on m.user_id=u.id
        order by u.created_at,u.email""")
    members = [{**dict(row), "id": str(row["id"]), "pending_invite": False} for row in rows]
    invites = await pool.fetch("""select id,email,role,requested_by,expires_at,created_at
        from public.workspace_invitations where accepted_at is null and expires_at>now()
        order by created_at,email""")
    members.extend({**dict(row), "id": str(row["id"]), "is_active": False,
                    "last_login_at": None, "pending_invite": True, "pending_role": None}
                   for row in invites)
    return members


async def invite_user(pool, settings, *, email: str, role: str, actor_id: str) -> dict:
    normalized = email.strip().lower()
    if role not in ROLES:
        raise HTTPException(status_code=422, detail="Choose admin, operator, or reviewer.")
    origin = settings.password_reset_origin.rstrip("/")
    if not origin:
        raise HTTPException(status_code=503, detail="Invitation email delivery is not configured on the server.")
    if await pool.fetchval("select exists(select 1 from public.users where email=$1)", normalized):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # Expired invitations may be replaced; valid invitations require explicit cancellation first.
    await pool.execute("delete from public.workspace_invitations where email=$1 and accepted_at is null and expires_at<=now()", normalized)
    token = secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        invitation = await pool.fetchrow("""insert into public.workspace_invitations
            (email,role,token_hash,requested_by,expires_at)
            values($1,$2,$3,$4::uuid,now()+($5::int * interval '1 hour'))
            returning id,email,role,expires_at,created_at""",
            normalized, role, token_hash, actor_id, TEAM_CONFIRMATION_HOURS)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="A valid invitation for this email already exists.") from None
    link = f"{origin}/auth/accept-invite?token={quote(token)}"
    try:
        await _send_mail(settings, normalized, "You are invited to PocketPerks",
                         f"You have been invited to PocketPerks as {role}. Accept this invitation within {TEAM_CONFIRMATION_HOURS} hours and set your password:\n\n{link}\n\nIf you were not expecting this invitation, you can ignore this email.")
    except Exception:
        await pool.execute("delete from public.workspace_invitations where id=$1 and accepted_at is null", invitation["id"])
        raise
    return {**dict(invitation), "id": str(invitation["id"]), "pending_invite": True}


async def request_role_change(pool, settings, *, user_id: str, actor_id: str, role: str) -> dict:
    if role not in ROLES:
        raise HTTPException(status_code=422, detail="Choose admin, operator, or reviewer.")
    if user_id == actor_id:
        raise HTTPException(status_code=409, detail="You cannot change your own role.")
    origin = settings.password_reset_origin.rstrip("/")
    if not origin:
        raise HTTPException(status_code=503, detail="Role confirmation email is not configured on the server.")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(741902615)")
            member = await conn.fetchrow("""select u.id,u.email,u.is_active,m.role from public.users u
                join public.workspace_members m on m.user_id=u.id where u.id=$1::uuid for update of u,m""", user_id)
            if member is None:
                raise HTTPException(status_code=404, detail="Workspace user not found.")
            if not member["is_active"]:
                raise HTTPException(status_code=409, detail="Restore this user's access before changing their role.")
            if member["role"] == role:
                return {"email": member["email"], "role": role, "changed": False}
            await conn.execute("delete from public.workspace_role_change_tokens where user_id=$1 and accepted_at is null", user_id)

            # Remove elevated admin access immediately; waiting for acceptance could leave old privileges active.
            if member["role"] == "admin" and role != "admin":
                active_admins = await conn.fetchval("""select count(*) from public.users u
                    join public.workspace_members m on m.user_id=u.id where u.is_active and m.role='admin' and u.id<>$1::uuid""", user_id)
                if active_admins < 1:
                    raise HTTPException(status_code=409, detail="Keep at least one active workspace admin.")
                await conn.execute("update public.workspace_members set role=$2 where user_id=$1::uuid", user_id, role)
                await conn.execute("update public.refresh_sessions set revoked_at=now() where user_id=$1::uuid and revoked_at is null", user_id)
                immediate = True
            else:
                token = secrets.token_urlsafe(40)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                await conn.execute("""insert into public.workspace_role_change_tokens
                    (user_id,requested_by,new_role,token_hash,expires_at)
                    values($1::uuid,$2::uuid,$3,$4,now()+($5::int * interval '1 hour'))""",
                    user_id, actor_id, role, token_hash, TEAM_CONFIRMATION_HOURS)
                immediate = False

    if immediate:
        try:
            await _send_mail(settings, member["email"], "Your PocketPerks role was updated",
                             f"A workspace admin changed your role to {role}. The change took effect immediately for security. If you did not expect this, contact your workspace admin.")
        except Exception:
            return {"email": member["email"], "role": role, "changed": True, "notification_sent": False}
        return {"email": member["email"], "role": role, "changed": True, "notification_sent": True}

    link = f"{origin}/auth/confirm-role?token={quote(token)}"
    try:
        await _send_mail(settings, member["email"], "Confirm your PocketPerks role change",
                         f"A workspace admin requested that your PocketPerks role change from {member['role']} to {role}. The new role will apply only after you confirm it within {TEAM_CONFIRMATION_HOURS} hours:\n\n{link}\n\nIf you did not expect this request, ignore this email. Your current role will remain active.")
    except Exception:
        await pool.execute("delete from public.workspace_role_change_tokens where user_id=$1::uuid and accepted_at is null", user_id)
        raise
    return {"email": member["email"], "role": member["role"], "pending_role": role, "changed": False}


async def cancel_invitation(pool, invitation_id: str) -> None:
    result = await pool.execute("delete from public.workspace_invitations where id=$1::uuid and accepted_at is null", invitation_id)
    if result.endswith("0"):
        raise HTTPException(status_code=404, detail="Pending invitation not found.")


async def update_user(pool, *, user_id: str, actor_id: str, is_active: bool,
                      removed_by_admin: bool = False) -> dict:
    if user_id == actor_id:
        raise HTTPException(status_code=409, detail="You cannot change your own account access.")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(741902615)")
            row = await conn.fetchrow("""select u.id,u.email,u.is_active,m.role from public.users u
                join public.workspace_members m on m.user_id=u.id where u.id=$1::uuid for update of u,m""", user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Workspace user not found.")
            if row["role"] == "admin" and row["is_active"] and not is_active:
                active_admins = await conn.fetchval("""select count(*) from public.users u
                    join public.workspace_members m on m.user_id=u.id where u.is_active and m.role='admin' and u.id<>$1::uuid""", user_id)
                if active_admins < 1:
                    raise HTTPException(status_code=409, detail="Keep at least one active workspace admin.")
            await conn.execute("""update public.users set is_active=$2,access_removed_by_admin=$3,
                updated_at=now() where id=$1::uuid""", user_id, is_active, removed_by_admin)
            if not is_active:
                await conn.execute("delete from public.workspace_role_change_tokens where user_id=$1::uuid and accepted_at is null", user_id)
                await conn.execute("update public.refresh_sessions set revoked_at=now() where user_id=$1::uuid and revoked_at is null", user_id)
            updated = await conn.fetchrow("""select u.id,u.email,u.is_active,u.access_removed_by_admin,
                u.created_at,u.last_login_at,m.role
                from public.users u join public.workspace_members m on m.user_id=u.id where u.id=$1::uuid""", user_id)
    return {**dict(updated), "id": str(updated["id"])}


async def remove_user(pool, *, user_id: str, actor_id: str) -> None:
    # Preserve historical campaign/review records while removing login access.
    await update_user(pool, user_id=user_id, actor_id=actor_id, is_active=False, removed_by_admin=True)


async def permanently_delete_user(pool, *, user_id: str, actor_id: str) -> None:
    """Delete a member's identity and auth data while preserving workspace history."""
    if user_id == actor_id:
        raise HTTPException(status_code=409, detail="You cannot permanently delete your own account.")
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialize with role/access changes so concurrent admin removals cannot
            # accidentally leave the workspace without an active administrator.
            await conn.execute("select pg_advisory_xact_lock(741902615)")
            member = await conn.fetchrow("""select u.id,u.is_active,m.role from public.users u
                join public.workspace_members m on m.user_id=u.id
                where u.id=$1::uuid for update of u,m""", user_id)
            if member is None:
                raise HTTPException(status_code=404, detail="Workspace user not found.")
            if member["role"] == "admin" and member["is_active"]:
                active_admins = await conn.fetchval("""select count(*) from public.users u
                    join public.workspace_members m on m.user_id=u.id
                    where u.is_active and m.role='admin' and u.id<>$1::uuid""", user_id)
                if active_admins < 1:
                    raise HTTPException(status_code=409, detail="Keep at least one active workspace admin.")
            # Auth rows and membership cascade. Audit actor references use SET NULL;
            # campaigns, leads, and review history are workspace-owned and remain.
            await conn.execute("delete from public.users where id=$1::uuid", user_id)
