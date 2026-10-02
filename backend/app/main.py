import logging
import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import (
    AUTOMATION_RESUME_ON_START,
    CORS_ORIGINS,
    DELIVERY_RETENTION_DAYS,
    DELIVERY_RETENTION_ENABLED,
    DELIVERY_RETENTION_INTERVAL_SECONDS,
    DELIVERY_RETENTION_MAX_RECORDS,
)
from app.routes import auth, automation, licenses, messages, session, settings, telegram
from app.services.app_session_service import AppSessionService
from app.services.automation_manager import AutomationManager
from app.services.storage_service import StorageService
from app.services.telegram_manager import TelegramClientManager
from app.services.license_service import LicenseService
from app.services.runtime_store import RuntimeStore
from app.utils.logger import EventLog
from app.utils.rate_limit import SlidingWindowLimiter
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    license_service = LicenseService()
    storage = StorageService()
    telegram_manager = TelegramClientManager(storage)
    events = EventLog()
    runtime = RuntimeStore()
    automation_manager = AutomationManager(telegram_manager, runtime, license_service)
    app.state.storage, app.state.telegram = storage, telegram_manager
    app.state.events, app.state.automation, app.state.licenses = events, automation_manager, license_service
    app.state.runtime, app.state.rate_limiter = runtime, SlidingWindowLimiter()
    app.state.app_sessions = AppSessionService()
    app.state.started_at = time.monotonic()
    if AUTOMATION_RESUME_ON_START:
        try:
            await automation_manager.recover_eligible()
        except Exception as exc:
            logging.getLogger("tele-automation").exception("Automation recovery failed safely", exc_info=exc)
            events.audit("automation_recovery_failed")
    cleanup_task = asyncio.create_task(_license_cleanup_loop(license_service))
    retention_task = (asyncio.create_task(_delivery_cleanup_loop(runtime, events))
                      if DELIVERY_RETENTION_ENABLED else None)
    yield
    cleanup_task.cancel()
    if retention_task:
        retention_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task
    if retention_task:
        with suppress(asyncio.CancelledError):
            await retention_task
    await automation_manager.shutdown()
    await telegram_manager.shutdown()


app = FastAPI(title="TELE AUTOMATION API", version="1.1.0", description="Local-first Telegram automation API for scheduled multi-destination delivery, monitoring, and license validation.", docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    error = exc.errors()[0] if exc.errors() else {}
    message = error.get("msg", "Invalid request.").replace("Value error, ", "")
    return JSONResponse(status_code=422, content={"detail": message})


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    logging.getLogger("tele-automation").exception("Unhandled API error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error. Check backend logs."})


@app.get("/api/health", tags=["system"])
async def health(request: Request):
    return {"status": "ok", "service": "tele-automation",
            "telegram_configured": bool((await request.app.state.storage.get_settings()).get("api_id")),
            "license_backend": "firebase" if request.app.state.licenses._firebase else "local",
            "runtime_backend": request.app.state.runtime.backend,
            "uptime_seconds": round(time.monotonic() - request.app.state.started_at, 1)}


@app.get("/api/ready", tags=["system"])
async def ready(request: Request):
    telegram_configured = bool((await request.app.state.storage.get_settings()).get("api_id"))
    return {"ready": bool(telegram_configured and request.app.state.licenses.configured), "telegram_configured": telegram_configured, "licenses": request.app.state.licenses.configured}


@app.get("/api/metrics", tags=["system"])
async def metrics(request: Request):
    return {"uptime_seconds": round(time.monotonic() - request.app.state.started_at, 1),
            "active_automations": request.app.state.automation.active_count,
            "active_telegram_contexts": request.app.state.telegram.active_count,
            "runtime_backend": request.app.state.runtime.backend}


@app.get("/api/docs/postman.json", tags=["documentation"], summary="Download Postman collection")
async def postman_collection(request: Request):
    base = "{{baseUrl}}"
    deployed_base = str(request.base_url).rstrip("/") + "/api"
    return {"info": {"name": "Tele Automation API", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"}, "variable": [{"key": "baseUrl", "value": deployed_base}, {"key": "licenseKey", "value": "123456789012"}], "item": [{"name": "Health", "request": {"method": "GET", "url": base + "/health"}}, {"name": "Validate license", "request": {"method": "POST", "header": [{"key": "Content-Type", "value": "application/json"}], "body": {"mode": "raw", "raw": "{\n  \"key\": \"{{licenseKey}}\"\n}"}, "url": base + "/licenses/validate"}}, {"name": "Automation status", "request": {"method": "GET", "url": base + "/automation/status"}}, {"name": "Deliveries", "request": {"method": "GET", "url": base + "/automation/deliveries?limit=100"}}]}


async def _license_cleanup_loop(service: LicenseService):
    while True:
        try:
            await service.cleanup()
        except Exception:
            logging.getLogger("tele-automation").exception("License cleanup failed")
        await asyncio.sleep(60)


async def _delivery_cleanup_loop(runtime: RuntimeStore, events: EventLog):
    while True:
        try:
            removed = await asyncio.to_thread(
                runtime.cleanup_all_deliveries, DELIVERY_RETENTION_DAYS, DELIVERY_RETENTION_MAX_RECORDS
            )
            if removed:
                events.add("INFO", "delivery_retention_cleanup", f"Removed {removed} expired delivery record(s)")
                events.audit("delivery_retention_cleanup", removed=removed)
        except Exception:
            logging.getLogger("tele-automation").exception("Delivery retention cleanup failed")
        await asyncio.sleep(DELIVERY_RETENTION_INTERVAL_SECONDS)


for route in (session.router, settings.router, auth.router, telegram.router, messages.router, automation.router, licenses.router):
    app.include_router(route, prefix="/api")
