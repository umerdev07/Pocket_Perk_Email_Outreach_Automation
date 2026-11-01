import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from fastapi import HTTPException as FastAPIHTTPException

from app.config import Settings, get_settings
from app.db import check_database
from app.auth import allowed_roles, authenticate_request
from app.routers.leads import batch_router, router as leads_router
from app.routers.campaigns import router as campaigns_router
from app.routers.drafts import router as drafts_router
from app.routers.campaign_runs import router as campaign_runs_router, runs_router
from app.routers.approvals import router as approvals_router
from app.routers.dashboard import router as dashboard_router
from app.services.campaign_queue_worker import run_campaign_queue_worker


def create_app(*, auth_enabled: bool = True, queue_worker_enabled: bool = False) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker_task = None
        if queue_worker_enabled and settings.supabase_url and settings.supabase_service_key:
            worker_task = asyncio.create_task(run_campaign_queue_worker(settings), name="pocketperks-pgmq-worker")
        yield
        if worker_task:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)

    app = FastAPI(title="PocketPerks API", lifespan=lifespan)
    app.state.queue_worker_enabled = queue_worker_enabled
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
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def require_workspace_access(request: Request, call_next):
        path = request.url.path
        if not auth_enabled or not path.startswith("/api/") or path == "/api/health" or request.method == "OPTIONS":
            return await call_next(request)
        try:
            user = await authenticate_request(request, settings)
            if user["role"] not in allowed_roles(path, request.method):
                return JSONResponse(status_code=403, content={"detail": "Your workspace role cannot perform this action."})
            request.state.workspace_user = user
        except FastAPIHTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
        return await call_next(request)

    @app.middleware("http")
    async def harden_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

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

    @app.get("/api/auth/me")
    async def current_workspace_user(request: Request):
        return request.state.workspace_user

    return app


app = create_app(queue_worker_enabled=True)
