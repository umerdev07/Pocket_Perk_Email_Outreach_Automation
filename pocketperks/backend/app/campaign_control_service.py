"""Pause and resume Instantly campaigns while keeping scheduler state in sync."""

from datetime import UTC, datetime

from fastapi import HTTPException

from app.campaign_control_lock import campaign_control_lock
from app.integrations.instantly_client import activate_campaign, pause_campaign


async def _load_mappings(pool, *, campaign_id=None, run_id=None, status):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select m.id,m.run_id,m.instantly_campaign_id,m.status,
                      c.start_at,coalesce(r.schedule_end_at,c.end_at) end_at
                 from public.instantly_campaign_mappings m
                 join public.campaign_runs r on r.id=m.run_id
                 join public.campaigns c on c.id=r.campaign_id
                where m.provider_deleted_at is null and m.instantly_campaign_id is not null
                  and m.status=any($1::text[])
                  and ($2::uuid is null or r.campaign_id=$2::uuid)
                  and ($3::uuid is null or r.id=$3::uuid)
                order by m.updated_at""",
            status, campaign_id, run_id,
        )
    return [dict(row) for row in rows]


async def pause_campaigns(settings, pool, *, campaign_id=None, run_id=None):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is temporarily unavailable.")
    if not settings.instantly_api_key:
        raise HTTPException(status_code=503, detail="Instantly is not configured.")
    mappings = await _load_mappings(
        pool, campaign_id=campaign_id, run_id=run_id,
        status=["active", "scheduled", "launching"],
    )
    if not mappings:
        raise HTTPException(status_code=409, detail="No active or scheduled Instantly campaign was found to pause.")

    paused = 0
    failures = []
    for mapping in mappings:
        async with campaign_control_lock(pool, str(mapping["id"])) as conn:
            current = await conn.fetchrow("""select status,instantly_campaign_id
                from public.instantly_campaign_mappings where id=$1::uuid""", str(mapping["id"]))
            if not current or current["status"] not in {"active", "scheduled", "launching"}:
                continue
            try:
                # For scheduled rows this local transition prevents the worker
                # from activating the provider draft. The advisory lock ensures
                # a concurrent activation completes before an active pause.
                if current["status"] in {"active", "launching"}:
                    await pause_campaign(str(current["instantly_campaign_id"]), settings)
                await conn.execute("""update public.instantly_campaign_mappings
                    set status='paused',error_detail=null,updated_at=now() where id=$1::uuid""",
                    str(mapping["id"]))
                paused += 1
            except Exception as exc:
                failures.append(type(exc).__name__)

    if failures:
        detail = "Instantly could not confirm the pause for every campaign. Refresh the page and check Instantly before retrying."
        if paused:
            detail = f"{paused} campaign(s) paused; the remaining pause could not be confirmed. Check Instantly before retrying."
        raise HTTPException(status_code=502, detail=detail)
    return {"status": "paused", "campaigns_updated": paused}


async def resume_campaigns(settings, pool, *, campaign_id=None, run_id=None):
    if pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is temporarily unavailable.")
    if not settings.instantly_api_key:
        raise HTTPException(status_code=503, detail="Instantly is not configured.")
    if settings.send_mode != "live":
        raise HTTPException(status_code=409, detail="Set SEND_MODE=live before resuming sending.")
    mappings = await _load_mappings(
        pool, campaign_id=campaign_id, run_id=run_id, status=["paused"],
    )
    if not mappings:
        raise HTTPException(status_code=409, detail="No paused Instantly campaign was found to resume.")

    resumed = 0
    failures = []
    now = datetime.now(UTC)
    for mapping in mappings:
        async with campaign_control_lock(pool, str(mapping["id"])) as conn:
            current = await conn.fetchrow("""select m.status,m.instantly_campaign_id,c.start_at,
                    coalesce(r.schedule_end_at,c.end_at) end_at
                from public.instantly_campaign_mappings m
                join public.campaign_runs r on r.id=m.run_id
                join public.campaigns c on c.id=r.campaign_id
                where m.id=$1::uuid""", str(mapping["id"]))
            if not current or current["status"] != "paused":
                continue
            end_at = current["end_at"]
            if end_at is not None and end_at.astimezone(UTC) <= now:
                failures.append("expired")
                continue
            start_at = current["start_at"]
            if start_at is not None and start_at.astimezone(UTC) > now:
                if not settings.instantly_auto_launch:
                    failures.append("auto_launch_off")
                    continue
                # Leave the provider campaign as a draft until its start time.
                await conn.execute("""update public.instantly_campaign_mappings
                    set status='scheduled',error_detail=null,updated_at=now() where id=$1::uuid""",
                    str(mapping["id"]))
                resumed += 1
                continue

            try:
                await activate_campaign(str(current["instantly_campaign_id"]), settings)
                await conn.execute("""update public.instantly_campaign_mappings
                    set status='active',error_detail=null,updated_at=now() where id=$1::uuid""",
                    str(mapping["id"]))
                resumed += 1
            except Exception as exc:
                # Activation timeouts have an unknown outcome. Keep the mapping
                # paused so the UI does not claim success without confirmation.
                failures.append(type(exc).__name__)

    if failures:
        if not resumed and "expired" in failures:
            raise HTTPException(status_code=409, detail="This campaign's sending window has ended; it cannot be resumed.")
        if not resumed and "auto_launch_off" in failures:
            raise HTTPException(status_code=409, detail="Automatic campaign start is off. Enable it before resuming a future campaign.")
        if "expired" in failures:
            detail = "This campaign's sending window has ended; it cannot be resumed."
        elif "auto_launch_off" in failures:
            detail = "Automatic campaign start is off. Enable it before resuming a future campaign."
        else:
            detail = "Instantly could not confirm the resume. Check Instantly before retrying."
        if resumed:
            detail = f"{resumed} campaign(s) resumed; some could not be confirmed. Check Instantly."
        raise HTTPException(status_code=502, detail=detail)
    return {"status": "resumed", "campaigns_updated": resumed}
