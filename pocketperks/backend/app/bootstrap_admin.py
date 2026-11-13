"""Create the first invite-only workspace admin in a migrated PostgreSQL database."""

import asyncio
import os

import asyncpg

from app.auth_postgres import hash_password


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not database_url or not email or not password:
        raise SystemExit("Set DATABASE_URL, BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD first.")
    if "@" not in email or len(email) > 320:
        raise SystemExit("BOOTSTRAP_ADMIN_EMAIL must be a valid email address.")
    password_hash = hash_password(password)
    conn = await asyncpg.connect(database_url, timeout=15)
    try:
        async with conn.transaction():
            user_id = await conn.fetchval("""insert into public.users(email,password_hash)
                values($1,$2) on conflict(email) do nothing returning id""", email, password_hash)
            if user_id is None:
                raise SystemExit("That email already exists; bootstrap did not change its password or role.")
            await conn.execute("insert into public.workspace_members(user_id,role) values($1,'admin')", user_id)
        print(f"Workspace administrator created: {email}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
