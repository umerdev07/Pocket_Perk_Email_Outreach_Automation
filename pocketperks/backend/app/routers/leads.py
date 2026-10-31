import csv
import io
import re
from urllib.parse import urlparse
from uuid import UUID

import httpx
from openpyxl import load_workbook
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.config import Settings, get_settings
from app.lead_batches_db import delete_lead_batch, get_batch_lead_personalized_emails, get_lead_batch_details

router = APIRouter(prefix="/api/leads", tags=["leads"])
batch_router = APIRouter(tags=["lead batches"])
REQUIRED_HEADERS = [
    "Business name", "Business category", "Business email", "Phone number",
    "Website URL", "City", "Full address", "Email source URL",
    "Email verification result", "Verification tool", "Quality flags",
]
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
FIELD_KEYS = ("business_name", "business_category", "email", "phone", "website", "city", "full_address",
              "email_source_url", "verification_result", "verification_tool", "quality_flags")


@batch_router.delete("/api/lead-batches/{batch_id}", status_code=204)
async def delete_lead_batch_endpoint(batch_id: UUID, settings: Settings = Depends(get_settings)):
    await delete_lead_batch(settings, str(batch_id))


@batch_router.get("/api/lead-batches/{batch_id}/details")
async def lead_batch_details(batch_id: UUID, view: str = Query(default="imported", pattern="^(imported|not_imported)$"),
                             limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
                             settings: Settings = Depends(get_settings)):
    return await get_lead_batch_details(settings, str(batch_id), view=view, limit=limit, offset=offset)


@batch_router.get("/api/lead-batches/{batch_id}/leads/{lead_id}/personalized-emails")
async def batch_lead_personalized_emails(batch_id: UUID, lead_id: UUID,
                                         settings: Settings = Depends(get_settings)):
    return await get_batch_lead_personalized_emails(settings, str(batch_id), str(lead_id))


@batch_router.get("/api/lead-batches")
async def list_lead_batches(settings: Settings = Depends(get_settings)):
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for lead batches.")
    key = settings.supabase_service_key
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/lead_batches",
            params={"select": "id,file_name,imported_count,created_at", "order": "created_at.desc"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
    if not response.is_success:
        raise HTTPException(status_code=503, detail="Could not load lead batches.")
    return response.json()


@batch_router.get("/api/lead-batches/{batch_id}/report")
async def get_lead_batch_report(batch_id: UUID, limit: int = Query(default=10, ge=1, le=10),
                               settings: Settings = Depends(get_settings)):
    if limit not in (5, 10):
        raise HTTPException(status_code=422, detail="Report limit must be 5 or 10.")
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for lead reports.")
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    base = settings.supabase_url.rstrip("/") + "/rest/v1"
    async with httpx.AsyncClient(timeout=15) as client:
        batch_response = await client.get(f"{base}/lead_batches", params={
            "select": "id,file_name,total_rows,imported_count,duplicate_count,invalid_count,rejected_count,reject_log,created_at",
            "id": f"eq.{batch_id}", "limit": "1",
        }, headers=headers)
        if not batch_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load the import report.")
        batches = batch_response.json()
        if not batches:
            raise HTTPException(status_code=404, detail="Lead batch not found.")
        leads_response = await client.get(f"{base}/leads", params={
            "select": "business_name,business_category,email,city,verification_result,quality_flags",
            "batch_id": f"eq.{batch_id}", "order": "created_at.asc", "limit": str(limit),
        }, headers=headers)
        if not leads_response.is_success:
            raise HTTPException(status_code=503, detail="Could not load report lead rows.")
    return {"batch": batches[0], "leads": leads_response.json(), "shown": len(leads_response.json())}


def _domain(website: str) -> str | None:
    website = website.strip()
    if not website:
        return None
    parsed = urlparse(website if "://" in website else f"//{website}")
    host = parsed.hostname
    return host.lower().removeprefix("www.") if host else None


async def _existing_values(settings: Settings, emails: set[str], domains: set[str]) -> tuple[set[str], set[str]]:
    if not settings.supabase_url or not settings.supabase_service_key or (not emails and not domains):
        return set(), set()
    headers = {"apikey": settings.supabase_service_key, "Authorization": f"Bearer {settings.supabase_service_key}"}
    found_emails: set[str] = set()
    found_domains: set[str] = set()
    async with httpx.AsyncClient(timeout=10) as client:
        for column, values, destination in (("email", emails, found_emails), ("domain", domains, found_domains)):
            if not values:
                continue
            response = await client.get(
                f"{settings.supabase_url.rstrip('/')}/rest/v1/leads",
                params={"select": column, column: f"in.({','.join(values)})"}, headers=headers,
            )
            if not response.is_success:
                raise HTTPException(status_code=503, detail="Could not check existing leads in the database.")
            destination.update(str(row[column]).lower() for row in response.json() if row.get(column))
    return found_emails, found_domains


async def persist_import(settings: Settings, file_name: str, rows: list[dict], counts: dict) -> str:
    """Create the batch and persist accepted leads; reject details stay on the batch."""
    if not settings.supabase_url or not settings.supabase_service_key:
        raise HTTPException(status_code=503, detail="Database is not configured for lead imports.")
    base = f"{settings.supabase_url.rstrip('/')}/rest/v1"
    key = settings.supabase_service_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Prefer": "return=representation"}
    rejected = [{"row": index, "status": row["status"], "data": {k: v for k, v in row.items() if k != "status"}}
                for index, row in enumerate(rows, start=2) if row["status"] in ("duplicate", "invalid", "rejected")]
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{base}/lead_batches", headers=headers, json={
            "file_name": file_name, "total_rows": len(rows), "imported_count": counts["imported"],
            "duplicate_count": counts["duplicate"], "invalid_count": counts["invalid"],
            "rejected_count": counts["rejected"], "reject_log": rejected,
        })
        if not response.is_success or not response.json():
            raise HTTPException(status_code=503, detail="Could not create the lead import batch.")
        batch_id = response.json()[0]["id"]
        accepted = [{k: v for k, v in row.items() if k not in ("status", "row")} | {"batch_id": batch_id}
                    for row in rows if row["status"] == "imported"]
        if accepted:
            inserted = await client.post(f"{base}/leads", headers=headers, json=accepted)
            if not inserted.is_success:
                raise HTTPException(status_code=503, detail="Batch was created, but lead rows could not be saved.")
    return batch_id


def _parse_csv(content: bytes) -> tuple[list[str] | None, list[list[object]]]:
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    values = list(reader)
    if not values:
        return None, []
    return values[0], values[1:]


def _parse_xlsx(content: bytes) -> tuple[list[str] | None, list[list[object]]]:
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.worksheets[0] if workbook.worksheets else None
    values = list(sheet.iter_rows(values_only=True)) if sheet else []
    workbook.close()
    if not values:
        return None, []
    return ["" if value is None else str(value).strip() for value in values[0]], [list(row) for row in values[1:]]


def _validate_and_map(headers: list[str] | None, source_rows: list[list[object]]) -> list[dict]:
    if not source_rows and headers is None:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if not headers or not any(str(header).strip() for header in headers):
        raise HTTPException(status_code=400, detail="The uploaded file has no header row.")
    header_positions = {str(header).strip(): index for index, header in enumerate(headers)}
    missing = [header for header in REQUIRED_HEADERS if header not in header_positions]
    if missing:
        raise HTTPException(status_code=400, detail="The file is missing required columns: " + ", ".join(missing))
    rows = []
    for line, source in enumerate(source_rows, start=2):
        row = {key: ("" if source[header_positions[header]] is None else str(source[header_positions[header]])).strip()
               if header_positions[header] < len(source) else ""
               for header, key in zip(REQUIRED_HEADERS, FIELD_KEYS)}
        row["email"] = row["email"].lower()
        row["domain"] = _domain(row["website"])
        row["row"] = line
        rows.append(row)
    return rows


@router.post("/import")
async def import_leads(file: UploadFile = File(...), settings: Settings = Depends(get_settings)):
    extension = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if extension not in {"csv", "xlsx"}:
        raise HTTPException(status_code=415, detail="Upload a .csv or .xlsx file.")
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        headers, source_rows = _parse_csv(content) if extension == "csv" else _parse_xlsx(content)
        rows = _validate_and_map(headers, source_rows)
    except (UnicodeDecodeError, csv.Error):
        raise HTTPException(status_code=400, detail="CSV must be valid UTF-8 text.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="The uploaded file could not be read as a valid CSV or XLSX.") from exc

    valid_candidates = [r for r in rows if r["business_name"] and EMAIL_RE.fullmatch(r["email"])]
    db_emails, db_domains = await _existing_values(settings, {r["email"] for r in valid_candidates},
                                                   {r["domain"] for r in valid_candidates if r["domain"]})
    seen_emails, seen_domains = set(db_emails), set(db_domains)
    preview, counts = [], {"imported": 0, "duplicate": 0, "invalid": 0, "rejected": 0}
    for row in rows:
        status = "valid"
        if not row["business_name"]:
            status = "rejected"
        elif not EMAIL_RE.fullmatch(row["email"]):
            status = "invalid"
        elif row["email"] in seen_emails or (row["domain"] and row["domain"] in seen_domains):
            status = "duplicate"
        else:
            status = "imported"
            seen_emails.add(row["email"])
            if row["domain"]:
                seen_domains.add(row["domain"])
        counts[status] += 1
        row["status"] = status
        if len(preview) < 20:
            preview.append({k: v for k, v in row.items() if k != "row"})
    batch_id = await persist_import(settings, file.filename, rows, counts)
    return {"batch_id": batch_id, "preview": preview, "summary": counts, "total_rows": len(rows)}
