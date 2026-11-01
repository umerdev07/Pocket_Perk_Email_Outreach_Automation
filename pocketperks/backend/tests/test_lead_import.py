import io
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook
from fastapi.testclient import TestClient

from app.main import create_app

HEADERS = "Business name,Business category,Business email,Phone number,Website URL,City,Full address,Email source URL,Email verification result,Verification tool,Quality flags\n"


def test_import_normalizes_and_counts_duplicates_invalid_and_rejected():
    csv_data = HEADERS + "Cafe,coffee, TEST@EXAMPLE.CO.UK ,,,Leeds,,,,,\n" + "Cafe2,coffee,test@example.co.uk,,,Leeds,,,,,\n" + "Cafe3,coffee,broken,,,Leeds,,,,,\n" + ",coffee,x@y.co.uk,,,,,,,,\n"
    with patch("app.routers.leads._existing_values", new=AsyncMock(return_value=(set(), set()))):
        with patch("app.routers.leads.persist_import", new=AsyncMock(return_value="batch-123")) as persist:
            response = TestClient(create_app(auth_enabled=False)).post("/api/leads/import", files={"file": ("leads.csv", io.BytesIO(csv_data.encode()), "text/csv")})
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {"imported": 1, "duplicate": 1, "invalid": 1, "rejected": 1}
    assert body["preview"][0]["email"] == "test@example.co.uk"
    assert body["batch_id"] == "batch-123"
    assert persist.await_args.args[1] == "leads.csv"
    assert [r["status"] for r in persist.await_args.args[2]] == ["imported", "duplicate", "invalid", "rejected"]


def test_import_preview_is_limited_to_twenty_but_counts_all_rows():
    rows = "".join(f"Cafe{i},coffee,cafe{i}@example.co.uk,,https://www.cafe{i}.co.uk,Leeds,,,,,\n" for i in range(25))
    with patch("app.routers.leads._existing_values", new=AsyncMock(return_value=(set(), set()))):
        with patch("app.routers.leads.persist_import", new=AsyncMock(return_value="batch-1")):
            body = TestClient(create_app(auth_enabled=False)).post("/api/leads/import", files={"file": ("leads.csv", io.BytesIO((HEADERS + rows).encode()), "text/csv")}).json()
    assert len(body["preview"]) == 20
    assert body["total_rows"] == 25
    assert body["summary"]["imported"] == 25


def _xlsx_bytes(headers=HEADERS.strip().split(","), rows=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows or []:
        sheet.append(row)
    result = io.BytesIO()
    workbook.save(result)
    return result.getvalue()


def test_import_xlsx_first_sheet_uses_shared_normalization_and_save():
    data = _xlsx_bytes(rows=[["Cafe", "coffee", " TEST@EXAMPLE.CO.UK ", "", "https://www.cafe.co.uk", "Leeds", "", "", "", "", ""]])
    with patch("app.routers.leads._existing_values", new=AsyncMock(return_value=(set(), set()))):
        with patch("app.routers.leads.persist_import", new=AsyncMock(return_value="xlsx-batch")):
            response = TestClient(create_app(auth_enabled=False)).post("/api/leads/import", files={"file": ("leads.xlsx", io.BytesIO(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 200
    assert response.json()["batch_id"] == "xlsx-batch"
    assert response.json()["preview"][0]["email"] == "test@example.co.uk"
    assert response.json()["preview"][0]["domain"] == "cafe.co.uk"


def test_import_rejects_unsupported_file_type():
    response = TestClient(create_app(auth_enabled=False)).post("/api/leads/import", files={"file": ("leads.txt", b"data", "text/plain")})
    assert response.status_code == 415
    assert response.json()["detail"] == "Upload a .csv or .xlsx file."


def test_import_empty_file_and_missing_header_have_distinct_errors():
    client = TestClient(create_app(auth_enabled=False))
    empty = client.post("/api/leads/import", files={"file": ("empty.csv", b"", "text/csv")})
    no_header = client.post("/api/leads/import", files={"file": ("no-header.csv", b"\n", "text/csv")})
    missing = client.post("/api/leads/import", files={"file": ("missing.csv", b"Business name\nCafe\n", "text/csv")})
    assert empty.json()["detail"] == "The uploaded file is empty."
    assert no_header.json()["detail"] == "The uploaded file has no header row."
    assert "missing required columns" in missing.json()["detail"]
