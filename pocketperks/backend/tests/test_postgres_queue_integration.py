"""Optional integration checks for the native PostgreSQL job queue.

Set POSTGRES_TEST_DATABASE_URL to a disposable database before running this module.
"""

import asyncio
import json
import os
import re
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.postgres import apply_migrations
from app.postgres_queue import (
    claim_jobs,
    complete_job,
    enqueue_job,
    extend_job_lease,
    retry_job,
)
from app import leads_postgres
from app import campaigns_postgres, drafts_postgres
from app import auth_postgres
from app.config import Settings
from fastapi import HTTPException


DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Requires a disposable PostgreSQL test database")


def test_queue_claims_are_exclusive_and_retry_is_bounded():
    async def run():
        await apply_migrations(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        try:
            await pool.execute("delete from public.background_jobs where queue_name = 'personalization'")
            ids = [await enqueue_job(pool, "personalization", {"test": n}) for n in range(4)]

            claims = await asyncio.gather(
                claim_jobs(pool, "personalization", quantity=2),
                claim_jobs(pool, "personalization", quantity=2),
            )
            claimed_ids = [job["message_id"] for group in claims for job in group]
            assert len(claimed_ids) == len(set(claimed_ids)) == 4
            assert set(claimed_ids) == set(ids)

            target = ids[0]
            await pool.execute(
                "update public.background_jobs set lease_expires_at = now() - interval '1 second' where id = $1",
                target,
            )
            reclaimed = await claim_jobs(pool, "personalization", quantity=1)
            assert reclaimed[0]["message_id"] == target
            assert reclaimed[0]["read_count"] == 2

            assert await extend_job_lease(pool, target, lease_seconds=60)
            assert await retry_job(pool, target, "temporary", delay_seconds=0, max_attempts=3) == "pending"
            retried = await claim_jobs(pool, "personalization", quantity=1)
            assert retried[0]["message_id"] == target
            assert await retry_job(pool, target, "last failure", delay_seconds=0, max_attempts=3) == "dead"

            other = next(job_id for job_id in ids if job_id != target)
            assert await complete_job(pool, other)

            stale = next(job_id for job_id in ids if job_id not in {target, other})
            await pool.execute(
                "update public.background_jobs set lease_expires_at = now() - interval '1 second', attempts = 8 where id = $1",
                stale,
            )
            assert await claim_jobs(pool, "personalization", quantity=10, max_attempts=8) == []
            status = await pool.fetchval("select status from public.background_jobs where id = $1", stale)
            assert status == "dead"
        finally:
            await pool.execute("delete from public.background_jobs where queue_name = 'personalization'")
            await pool.close()

    asyncio.run(run())


def test_campaign_schedule_routines_preserve_create_update_and_step_delete():
    async def run():
        await apply_migrations(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        campaign_id = None
        try:
            starts = datetime.now(timezone.utc) + timedelta(days=1)
            ends = starts + timedelta(days=7)
            campaign = {
                "name": "Postgres routine test",
                "total_emails": 2,
                "instructions": "Test campaign instructions",
                "start_at": starts.isoformat(),
                "end_at": ends.isoformat(),
                "timezone": "Europe/London",
                "daily_send_start": "09:00",
                "daily_send_end": "17:00",
                "sending_days": [1, 2, 3, 4, 5],
            }
            result = await pool.fetchval(
                "select public.create_campaign_with_schedule($1::jsonb, $2::jsonb, $3::jsonb)",
                json.dumps(campaign), json.dumps(["Introduction", "Follow up"]), json.dumps([0, 4320]),
            )
            result = json.loads(result) if isinstance(result, str) else result
            campaign_id = result["id"]
            assert len(result["steps"]) == 2
            assert result["steps"][1]["delay_minutes"] == 4320

            trigger_result = await pool.fetchval(
                "select public.set_campaign_step_triggers($1::jsonb)",
                json.dumps([{"step_id": result["steps"][1]["id"], "trigger_type": "after_conversation"}]),
            )
            trigger_result = json.loads(trigger_result) if isinstance(trigger_result, str) else trigger_result
            assert trigger_result["updated"] == 1

            campaign["name"] = "Updated PostgreSQL routine test"
            updated = await pool.fetchval(
                "select public.update_campaign_with_schedule($1::uuid, $2::jsonb, $3::jsonb, $4::jsonb)",
                campaign_id, json.dumps(campaign), json.dumps(["New intro", "New follow up"]),
                json.dumps([0, 4320]),
            )
            updated = json.loads(updated) if isinstance(updated, str) else updated
            assert updated["updated"] is True
            assert updated["name"] == campaign["name"]

            removed = await pool.fetchval(
                "select public.delete_campaign_step($1::uuid, $2::uuid)",
                campaign_id, updated["steps"][1]["id"],
            )
            removed = json.loads(removed) if isinstance(removed, str) else removed
            assert removed["deleted"] is True
            assert removed["total_emails"] == 1
        finally:
            if campaign_id:
                await pool.execute("delete from public.generic_drafts d using public.campaign_steps s where s.campaign_id = $1 and d.step_id = s.id", campaign_id)
                await pool.execute("delete from public.campaign_steps where campaign_id = $1", campaign_id)
                await pool.execute("delete from public.campaigns where id = $1", campaign_id)
            await pool.close()

    asyncio.run(run())


def test_suppression_record_release_and_resuppress_audit():
    async def run():
        await apply_migrations(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        email = "postgres-migration-check@example.test"
        try:
            await pool.execute("delete from public.email_suppression_events where email = $1", email)
            await pool.execute("delete from public.email_suppressions where email = $1", email)
            count = await pool.fetchval(
                "select public.record_email_suppressions($1::jsonb)",
                json.dumps([{"email": email, "source": "reply_manual", "reason": "Recipient opted out."}]),
            )
            assert count == 1
            released = await pool.fetchrow(
                "select * from public.release_email_suppression($1, $2, null)",
                email, "Reviewed request and confirmed release.",
            )
            assert released["active"] is False and released["already_released"] is False
            count = await pool.fetchval(
                "select public.record_email_suppressions($1::jsonb)",
                json.dumps([{"email": email, "source": "reply_auto", "reason": "Opt-out received again."}]),
            )
            assert count == 1
            assert await pool.fetchval("select active from public.email_suppressions where email=$1", email)
            event_count = await pool.fetchval("select count(*) from public.email_suppression_events where email=$1", email)
            assert event_count == 3
        finally:
            await pool.execute("delete from public.email_suppression_events where email = $1", email)
            await pool.execute("delete from public.email_suppressions where email = $1", email)
            await pool.close()

    asyncio.run(run())


def test_postgres_lead_batch_import_reads_and_safe_cleanup():
    async def run():
        await apply_migrations(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        batch_id = None
        try:
            imported = {"status": "imported", "business_name": "Test Cafe", "business_category": "Cafe",
                "email": "test-cafe@example.test", "phone": "", "website": "", "domain": None,
                "city": "York", "full_address": "", "email_source_url": "", "verification_result": "Valid",
                "verification_tool": "test", "quality_flags": "", "first_name": "Ava"}
            batch_id = await leads_postgres.persist_import(pool, "test.csv", [imported],
                {"imported": 1, "duplicate": 0, "invalid": 0, "rejected": 0, "suppressed": 0})
            assert len(await leads_postgres.list_batches(pool)) == 1
            details = await leads_postgres.batch_details(pool, batch_id, view="imported", limit=10, offset=0)
            assert details["total"] == 1 and details["rows"][0]["first_name"] == "Ava"
            report = await leads_postgres.batch_report(pool, batch_id, 5)
            assert report["shown"] == 1
            lead_id = details["rows"][0]["id"]
            assert (await leads_postgres.personalized_emails_for_batch_lead(pool, batch_id, str(lead_id)))["items"] == []
            preview = await leads_postgres.cleanup_preview(pool, batch_id)
            assert preview["found"] is True and preview["lead_count"] == 1
            await leads_postgres.delete_batch(pool, batch_id)
            assert await pool.fetchval("select count(*) from public.lead_batches where id=$1::uuid", batch_id) == 0
        finally:
            if batch_id:
                await pool.execute("delete from public.lead_batches where id=$1::uuid", batch_id)
            await pool.close()

    asyncio.run(run())


def test_postgres_campaign_and_draft_repositories_preserve_review_flow():
    async def run():
        await apply_migrations(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        campaign_id = None
        try:
            starts = datetime.now(timezone.utc) + timedelta(days=1)
            ends = starts + timedelta(days=10)
            campaign = {
                "name": "Postgres repository flow test",
                "total_emails": 2,
                "instructions": "Introduce PocketPerks and follow up politely.",
                "start_at": starts.isoformat(),
                "end_at": ends.isoformat(),
                "timezone": "Europe/London",
                "daily_send_start": "09:00:00",
                "daily_send_end": "17:00:00",
                "sending_days": [1, 2, 3, 4, 5],
                "status": "draft",
            }
            created = await campaigns_postgres.create_campaign(
                pool, campaign, ["Introduction", "Follow-up"], [0, 4320], ["scheduled", "scheduled"]
            )
            campaign_id = created["id"]
            assert len(created["steps"]) == 2

            draft_rows = []
            for step in created["steps"]:
                draft = await drafts_postgres.prepare_step_draft(pool, step["id"])
                draft_rows.append(await drafts_postgres.upsert_step_draft(
                    pool, step["id"], f"Subject {step['step_number']}", "Hi there, this is a test draft."
                ))
                assert draft["generation_status"] == "generating"
            loaded = await drafts_postgres.load_campaign(pool, campaign_id)
            assert len(loaded["campaign_steps"]) == 2
            assert loaded["campaign_steps"][0]["generic_drafts"]["subject"] == "Subject 1"
            assert (await drafts_postgres.get_draft_context(pool, str(draft_rows[0]["id"]))) is not None

            await drafts_postgres.approve_draft(pool, str(draft_rows[0]["id"]))
            approved = await drafts_postgres.approve_draft(pool, str(draft_rows[1]["id"]))
            assert approved["status"] == "approved"
            assert await pool.fetchval("select status from public.campaigns where id=$1::uuid", campaign_id) == "approved"

            await drafts_postgres.update_draft(pool, str(draft_rows[0]["id"]), "Edited subject", "Edited body")
            assert await pool.fetchval("select status from public.campaigns where id=$1::uuid", campaign_id) == "draft"

            deleted_step = await campaigns_postgres.delete_campaign_step(
                pool, campaign_id, str(created["steps"][1]["id"])
            )
            assert deleted_step["deleted"] is True and deleted_step["total_emails"] == 1
            await campaigns_postgres.delete_campaign(pool, campaign_id)
            campaign_id = None
        finally:
            if campaign_id:
                await pool.execute("delete from public.campaigns where id=$1::uuid", campaign_id)
            await pool.close()

    asyncio.run(run())


def test_postgres_auth_login_refresh_reset_and_two_address_email_change():
    async def run():
        await apply_migrations(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        email = f"auth-{os.getpid()}@example.test"
        changed_email = f"auth-changed-{os.getpid()}@example.test"
        user_id = None
        settings = Settings(_env_file=None, auth_jwt_secret="local-integration-test-secret-32-bytes-minimum",
                            password_reset_origin="https://app.example.test")
        try:
            user_id = await pool.fetchval("""insert into public.users(email,password_hash) values($1,$2) returning id""",
                email, auth_postgres.hash_password("First secure test password"))
            await pool.execute("insert into public.workspace_members(user_id,role) values($1,'admin')", user_id)

            first = await auth_postgres.sign_in(pool, settings, email.upper(), "First secure test password")
            first_payload = auth_postgres.verify_access_token(settings, first["access_token"])
            assert (await auth_postgres.workspace_user(pool, first_payload))["role"] == "admin"

            second = await auth_postgres.refresh_session(pool, settings, first["refresh_token"])
            with pytest.raises(HTTPException) as expired_access:
                await auth_postgres.workspace_user(pool, first_payload)
            assert expired_access.value.status_code == 401
            await auth_postgres.revoke_refresh_session(pool, second["refresh_token"])
            with pytest.raises(HTTPException):
                await auth_postgres.refresh_session(pool, settings, second["refresh_token"])

            with patch("app.auth_postgres._send_mail", new=AsyncMock()) as send_mail:
                await auth_postgres.request_password_reset(pool, settings, email)
                reset_body = send_mail.await_args.args[3]
                reset_token = re.search(r"token=([^\s]+)", reset_body).group(1)
                await auth_postgres.reset_password(pool, reset_token, "Second secure test password")

            with pytest.raises(HTTPException):
                await auth_postgres.sign_in(pool, settings, email, "First secure test password")
            await auth_postgres.sign_in(pool, settings, email, "Second secure test password")

            with patch("app.auth_postgres._send_mail", new=AsyncMock()) as send_mail:
                await auth_postgres.request_email_change(pool, settings, str(user_id), changed_email)
                tokens = [re.search(r"token=([^\s]+)", call.args[3]).group(1)
                          for call in send_mail.await_args_list]
            assert len(tokens) == 2
            first_confirmation = await auth_postgres.confirm_email_change(pool, tokens[0])
            assert first_confirmation == {"confirmed": True, "complete": False}
            second_confirmation = await auth_postgres.confirm_email_change(pool, tokens[1])
            assert second_confirmation == {"confirmed": True, "complete": True}
            final_session = await auth_postgres.sign_in(pool, settings, changed_email,
                                                        "Second secure test password")
            assert final_session["user"]["email"] == changed_email
        finally:
            if user_id:
                await pool.execute("delete from public.auth_login_attempts where email_hash=any($1::text[])",
                    [auth_postgres._email_digest(email), auth_postgres._email_digest(changed_email)])
                await pool.execute("delete from public.users where id=$1", user_id)
            await pool.close()

    asyncio.run(run())
