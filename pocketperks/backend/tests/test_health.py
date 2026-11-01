from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import check_database
from app.main import create_app


def test_health_connected():
    with patch("app.main.check_database", new=AsyncMock(return_value=True)):
        response = TestClient(create_app(auth_enabled=False)).get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == "connected"


def test_health_unavailable_does_not_claim_success():
    with patch("app.main.check_database", new=AsyncMock(return_value=False)):
        response = TestClient(create_app(auth_enabled=False)).get("/api/health")
    assert response.status_code == 503
    assert response.json()["backend"] == "ok"
    assert response.json()["database"] == "unavailable"
    assert response.json()["error"] == "database_unavailable"


def test_cors_for_frontend():
    response = TestClient(create_app(auth_enabled=False)).options("/api/health", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET",
    })
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_missing_database_config():
    import asyncio
    assert asyncio.run(check_database(Settings(_env_file=None, supabase_url="", supabase_service_key=""))) is False
