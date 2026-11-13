import csv
import io
import re
import zipfile
from urllib.parse import urlparse
from uuid import UUID

from openpyxl import load_workbook
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from app import leads_postgres

router = APIRouter(prefix="/api/leads", tags=["leads"])
batch_router = APIRouter(tags=["lead batches"])
REQUIRED_HEADERS = [
    "Business name", "Business category", "Business email", "Phone number",
    "Website URL", "City", "Full address", "Email source URL",
    "Email verification result", "Verification tool", "Quality flags",
]
OPTIONAL_HEADER_MAP = {"First name": "first_name", "Decision-maker first name": "first_name"}
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.I)
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000
MAX_XLSX_UNCOMPRESSED_BYTES = 60 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 100
MAX_XLSX_ENTRIES = 10_000
FIELD_KEYS = ("business_name", "business_category", "email", "phone", "website", "city", "full_address",
              "email_source_url", "verification_result", "verification_tool", "quality_flags")


@batch_router.delete("/api/lead-batches/{batch_id}", status_code=204)
async def delete_lead_batch_endpoint(batch_id: UUID, request: Request):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Lead storage is temporarily unavailable.")
    try:
        await leads_postgres.delete_batch(pool, str(batch_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return None


@batch_router.get("/api/lead-batches/{batch_id}/cleanup-preview")
async def lead_batch_cleanup_preview(batch_id: UUID, request: Request):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Lead storage is temporarily unavailable.")
    try:
        return await leads_postgres.cleanup_preview(pool, str(batch_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@batch_router.get("/api/lead-batches/{batch_id}/details")
async def lead_batch_details(batch_id: UUID, request: Request, view: str = Query(default="imported", pattern="^(imported|not_imported)$"),
                             limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Lead storage is temporarily unavailable.")
    try:
        return await leads_postgres.batch_details(pool, str(batch_id), view=view, limit=limit, offset=offset)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@batch_router.get("/api/lead-batches/{batch_id}/leads/{lead_id}/personalized-emails")
async def batch_lead_personalized_emails(batch_id: UUID, lead_id: UUID,
                                         request: Request):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Lead storage is temporarily unavailable.")
    try:
        return await leads_postgres.personalized_emails_for_batch_lead(pool, str(batch_id), str(lead_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@batch_router.get("/api/lead-batches")
async def list_lead_batches(request: Request):
    pool = getattr(request.app.state, "postgres_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Lead storage is temporarily unavailable.")
    return await leads_postgres.list_batches(pool)


@batch_router.get("/api/lead-batches/{batch_id}/report")
async def get_lead_batch_report(batch_id: UUID, limit: int = Query(default=10, ge=1, le=10),
                               request: Request = None):
    if limit not in (5, 10):
        raise HTTPException(status_code=422, detail="Report limit must be 5 or 10.")
    pool = getattr(request.app.state, "postgres_pool", None) if request else None
    if pool is None:
        raise HTTPException(status_code=503, detail="Lead storage is temporarily unavailable.")
    try:
        return await leads_postgres.batch_report(pool, str(batch_id), limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _domain(website: str) -> str | None:
    website = website.strip()
    if not website:
        return None
    parsed = urlparse(website if "://" in website else f"//{website}")
    host = parsed.hostname
    return host.lower().removeprefix("www.") if host else None


def _parse_csv(content: bytes) -> tuple[list[str] | None, list[list[object]]]:
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    headers = next(reader, None)
    if headers is None:
        return None, []
    rows = []
    for row in reader:
        if len(rows) >= MAX_IMPORT_ROWS:
            raise HTTPException(status_code=413, detail=f"A file can contain at most {MAX_IMPORT_ROWS:,} data rows.")
        rows.append(row)
    return headers, rows


def _parse_xlsx(content: bytes) -> tuple[list[str] | None, list[list[object]]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_XLSX_ENTRIES:
                raise HTTPException(status_code=400, detail="The Excel file contains too many internal components.")
            names = {member.filename for member in members}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise HTTPException(status_code=400, detail="The uploaded file is not a valid XLSX workbook.")
            expanded_size = sum(member.file_size for member in members)
            compressed_size = max(1, sum(member.compress_size for member in members))
            if expanded_size > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise HTTPException(status_code=413, detail="The expanded Excel file is too large to process safely.")
            if expanded_size / compressed_size > MAX_XLSX_COMPRESSION_RATIO:
                raise HTTPException(status_code=400, detail="The Excel file has an unsafe compression ratio.")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid XLSX workbook.") from exc

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0] if workbook.worksheets else None
        if sheet is None:
            return None, []
        values = []
        for row in sheet.iter_rows(values_only=True, max_col=len(REQUIRED_HEADERS) + len(OPTIONAL_HEADER_MAP)):
            if len(values) >= MAX_IMPORT_ROWS + 2:
                raise HTTPException(status_code=413, detail=f"A file can contain at most {MAX_IMPORT_ROWS:,} data rows.")
            values.append(list(row))
        if not values:
            return None, []
        return ["" if value is None else str(value).strip() for value in values[0]], values[1:]
    finally:
        workbook.close()


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
        for header, key in OPTIONAL_HEADER_MAP.items():
            if header in header_positions and not row.get(key):
                index = header_positions[header]
                row[key] = ("" if index >= len(source) or source[index] is None else str(source[index])).strip()
        row["email"] = row["email"].lower()
        row["domain"] = _domain(row["website"])
        row["row"] = line
        rows.append(row)
    return rows


@router.post("/import")
async def import_leads(file: UploadFile = File(...), request: Request = None):
    extension = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if extension not in {"csv", "xlsx"}:
        raise HTTPException(status_code=415, detail="Upload a .csv or .xlsx file.")
    try:
        content = bytearray()
        while chunk := await file.read(1024 * 1024):
            content.extend(chunk)
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Upload is too large. Maximum file size is 15 MB.")
        content = bytes(content)
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
    pool = getattr(request.app.state, "postgres_pool", None) if request else None
    if pool is None:
        raise HTTPException(status_code=503, detail="Lead storage is temporarily unavailable.")
    emails = {r["email"] for r in valid_candidates}
    domains = {r["domain"] for r in valid_candidates if r["domain"]}
    db_emails, db_domains = await leads_postgres.existing_values(pool, emails, domains)
    seen_emails, seen_domains = set(db_emails), set(db_domains)
    suppressed_emails = await leads_postgres.suppressed_emails(pool, emails)
    preview, counts = [], {"imported": 0, "duplicate": 0, "invalid": 0, "rejected": 0, "suppressed": 0}
    for row in rows:
        status = "valid"
        if not row["business_name"]:
            status = "rejected"
        elif not EMAIL_RE.fullmatch(row["email"]):
            status = "invalid"
        elif row["email"] in suppressed_emails:
            status = "rejected"
            row["rejection_reason"] = "This address previously opted out and is suppressed."
            counts["suppressed"] += 1
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
    batch_id = await leads_postgres.persist_import(pool, file.filename, rows, counts)
    return {"batch_id": batch_id, "preview": preview, "summary": counts, "total_rows": len(rows)}
