from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.config import Settings, get_settings
from app.db import check_database
from app.routers.leads import batch_router, router as leads_router
from app.routers.campaigns import router as campaigns_router
from app.routers.drafts import router as drafts_router
from app.routers.campaign_runs import router as campaign_runs_router, runs_router
from app.routers.approvals import router as approvals_router
from app.routers.dashboard import router as dashboard_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PocketPerks API")
    app.include_router(leads_router)
    app.include_router(batch_router)
    app.include_router(campaigns_router)
    app.include_router(drafts_router)
    app.include_router(campaign_runs_router)
    app.include_router(runs_router)
    app.include_router(approvals_router)
    app.include_router(dashboard_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": "request_error", "detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": "validation_error", "detail": "Please check the request fields."})

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": "An unexpected server error occurred."})

    @app.get("/api/health")
    async def health(config: Settings = Depends(get_settings)):
        connected = await check_database(config)
        payload = {
            "status": "ok" if connected else "degraded",
            "backend": "ok",
            "database": "connected" if connected else "unavailable",
            "send_mode": config.send_mode,
        }
        if not connected:
            payload.update(error="database_unavailable", detail="Check backend Supabase settings and run the initial migration.")
        return JSONResponse(status_code=200 if connected else 503, content=payload)

    return app


app = create_app()
