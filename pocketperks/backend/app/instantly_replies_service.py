"""Bounded, idempotent read-sync from Instantly Unibox into PocketPerks."""
import asyncio
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.integrations.instantly_client import list_received_emails
from app.replies_db import (get_reply_sync_state, save_received_replies,
                            update_reply_sync_state)

_SYNC_LOCK = asyncio.Lock()

async def sync_recent_replies(settings: Settings, *, max_pages: int = 1) -> dict[str, int]:
    if type(max_pages) is not int or not 1 <= max_pages <= 5:
        raise ValueError("Reply sync must use between one and five pages.")
    async with _SYNC_LOCK:
        return await _sync_recent_replies(settings, max_pages=max_pages)


async def _sync_recent_replies(settings: Settings, *, max_pages: int) -> dict[str, int]:
    state = await get_reply_sync_state(settings)
    now = datetime.now(UTC)
    sync_from = state.get("sync_from")
    if not sync_from:
        last_checked = datetime.fromisoformat(str(state["last_checked_at"]).replace("Z", "+00:00"))
        sync_from = max(last_checked - timedelta(hours=2), now - timedelta(days=30)).isoformat()
    cursor = state.get("starting_after")
    saved = fetched = 0
    seen_cursors: set[str] = set()
    for _ in range(max_pages):
        page = await list_received_emails(settings=settings, limit=100, cursor=cursor,
                                          min_timestamp_created=sync_from)
        items = page["items"]
        fetched += len(items)
        saved += await save_received_replies(settings, items)
        next_cursor = page.get("next_starting_after")
        if not next_cursor:
            await update_reply_sync_state(settings, last_checked_at=now.isoformat(),
                                          sync_from=None, starting_after=None)
            break
        if next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        # Save the cursor only after the page has been persisted. A restart can
        # safely repeat that page because provider email IDs are unique locally.
        await update_reply_sync_state(settings, sync_from=sync_from, starting_after=cursor)
    return {"fetched": fetched, "saved": saved}
