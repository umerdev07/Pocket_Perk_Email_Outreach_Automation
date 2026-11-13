"""Native PostgreSQL persistence for the lead-import workflow."""
import json


async def existing_values(pool, emails: set[str], domains: set[str]) -> tuple[set[str], set[str]]:
    if not emails and not domains:
        return set(), set()
    rows = await pool.fetch(
        "select email, domain from public.leads where email = any($1::text[]) or domain = any($2::text[])",
        sorted(emails), sorted(domains),
    )
    return ({row["email"].lower() for row in rows if row["email"]},
            {row["domain"].lower() for row in rows if row["domain"]})


async def suppressed_emails(pool, emails: set[str]) -> set[str]:
    if not emails:
        return set()
    rows = await pool.fetch(
        "select email from public.email_suppressions where active and email = any($1::text[])",
        sorted(emails),
    )
    return {row["email"].lower() for row in rows}


async def persist_import(pool, file_name: str, rows: list[dict], counts: dict) -> str:
    rejected = [{"row": index, "status": row["status"],
                 "data": {key: value for key, value in row.items() if key != "status"}}
                for index, row in enumerate(rows, start=2)
                if row["status"] in ("duplicate", "invalid", "rejected")]
    async with pool.acquire() as connection:
        async with connection.transaction():
            batch_id = await connection.fetchval(
                """insert into public.lead_batches
                   (file_name,total_rows,imported_count,duplicate_count,invalid_count,rejected_count,suppressed_count,reject_log)
                   values ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) returning id""",
                file_name, len(rows), counts["imported"], counts["duplicate"], counts["invalid"],
                counts["rejected"], counts.get("suppressed", 0), rejected,
            )
            accepted = [row for row in rows if row["status"] == "imported"]
            await connection.executemany(
                """insert into public.leads
                   (batch_id,first_name,business_name,business_category,email,phone,website,domain,city,full_address,
                    email_source_url,verification_result,verification_tool,quality_flags)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
                [(batch_id, row.get("first_name") or None, row["business_name"], row["business_category"],
                  row["email"], row["phone"], row["website"], row["domain"], row["city"], row["full_address"],
                  row["email_source_url"], row["verification_result"], row["verification_tool"], row["quality_flags"])
                 for row in accepted],
            )
    return str(batch_id)


async def list_batches(pool) -> list[dict]:
    rows = await pool.fetch("""select id,file_name,imported_count,suppressed_count,created_at
                              from public.lead_batches order by created_at desc,id desc""")
    return [dict(row) for row in rows]


async def batch_details(pool, batch_id: str, *, view: str, limit: int, offset: int) -> dict:
    batch = await pool.fetchrow("""select id,file_name,total_rows,imported_count,duplicate_count,
        invalid_count,rejected_count,suppressed_count,reject_log,created_at
        from public.lead_batches where id=$1::uuid""", batch_id)
    if batch is None:
        raise LookupError("Import batch not found.")
    batch = dict(batch)
    if view == "not_imported":
        rows = batch["reject_log"] or []
        total = len(rows)
        page = rows[offset:offset + limit]
    else:
        total = await pool.fetchval("select count(*) from public.leads where batch_id=$1::uuid", batch_id)
        records = await pool.fetch("""select id,business_name,first_name,business_category,email,phone,website,
            domain,city,full_address,email_source_url,verification_result,verification_tool,quality_flags
            from public.leads where batch_id=$1::uuid order by created_at,id limit $2 offset $3""",
            batch_id, limit, offset)
        page = [dict(row) for row in records]
    batch.pop("reject_log", None)
    return {"batch": batch, "view": view, "rows": page, "total": total, "limit": limit,
            "offset": offset, "has_more": offset + len(page) < total}


async def batch_report(pool, batch_id: str, limit: int) -> dict:
    batch = await pool.fetchrow("""select id,file_name,total_rows,imported_count,duplicate_count,
        invalid_count,rejected_count,suppressed_count,reject_log,created_at
        from public.lead_batches where id=$1::uuid""", batch_id)
    if batch is None:
        raise LookupError("Import batch not found.")
    rows = await pool.fetch("""select business_name,business_category,email,city,verification_result,quality_flags
        from public.leads where batch_id=$1::uuid order by created_at limit $2""", batch_id, limit)
    data = dict(batch)
    data.pop("reject_log", None)
    leads = [dict(row) for row in rows]
    return {"batch": data, "leads": leads, "shown": len(leads)}


async def personalized_emails_for_batch_lead(pool, batch_id: str, lead_id: str) -> dict:
    lead = await pool.fetchrow("""select id,batch_id,business_name,first_name,business_category,email,city,website
        from public.leads where id=$1::uuid and batch_id=$2::uuid""", lead_id, batch_id)
    if lead is None:
        raise LookupError("Lead not found in this import batch.")
    rows = await pool.fetch("""select e.id,e.lead_id,e.campaign_id,e.step_id,e.run_id,e.subject,e.body,e.status,
        e.risk_level,e.risk_reason,e.created_at,c.name as campaign_name,s.step_number,s.context
        from public.personalized_emails e
        join public.campaign_runs r on r.id=e.run_id
        join public.campaigns c on c.id=e.campaign_id
        join public.campaign_steps s on s.id=e.step_id
        where r.batch_id=$1::uuid and e.lead_id=$2::uuid
        order by c.name,s.step_number,e.created_at desc limit 1000""", batch_id, lead_id)
    items = []
    for row in rows:
        item = dict(row)
        item["campaign"] = {"id": str(item["campaign_id"]), "name": item.pop("campaign_name")}
        item["step"] = {"id": str(item["step_id"]), "step_number": item.pop("step_number"),
                        "context": item.pop("context")}
        item["id"] = str(item["id"])
        item["lead_id"] = str(item["lead_id"])
        item["campaign_id"] = str(item["campaign_id"])
        item["step_id"] = str(item["step_id"])
        item["run_id"] = str(item["run_id"])
        items.append(item)
    return {"lead": dict(lead), "items": items}


async def cleanup_preview(pool, batch_id: str) -> dict:
    result = await pool.fetchval("select public.preview_lead_batch_cleanup($1::uuid)", batch_id)
    if isinstance(result, str):
        result = json.loads(result)
    if not result or result.get("found") is not True:
        raise LookupError("Import batch not found.")
    return result


async def delete_batch(pool, batch_id: str) -> None:
    result = await pool.fetchval("select public.delete_lead_batch_and_related_data($1::uuid)", batch_id)
    if isinstance(result, str):
        result = json.loads(result)
    reason = (result or {}).get("reason")
    if reason == "not_found":
        raise LookupError("Lead batch not found.")
    if reason == "active_run":
        raise RuntimeError("This batch has a campaign run in progress. Wait for it to finish before deleting.")
    if reason == "active_provider_campaign":
        raise RuntimeError("An Instantly campaign linked to this batch is still active. Pause or stop it before cleaning this activity.")
    if (result or {}).get("deleted") is not True:
        raise RuntimeError("Could not confirm lead batch cleanup.")
