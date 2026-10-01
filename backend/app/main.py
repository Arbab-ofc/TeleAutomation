import logging
import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import (
    CORS_ORIGINS,
    DELIVERY_RETENTION_DAYS,
    DELIVERY_RETENTION_ENABLED,
    DELIVERY_RETENTION_INTERVAL_SECONDS,
    DELIVERY_RETENTION_MAX_RECORDS,
)
from app.routes import auth, automation, licenses, messages, settings, telegram
from app.services.automation_service import AutomationService
from app.services.storage_service import StorageService
from app.services.telegram_service import TelegramService
from app.services.license_service import LicenseService
from app.services.runtime_store import RuntimeStore
from app.utils.logger import EventLog
from app.utils.rate_limit import SlidingWindowLimiter
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = StorageService()
    telegram_service = TelegramService(storage)
    events = EventLog()
    license_service = LicenseService()
    runtime = RuntimeStore()
    automation_service = AutomationService(telegram_service, events, runtime, license_service)
    app.state.storage, app.state.telegram = storage, telegram_service
    app.state.events, app.state.automation, app.state.licenses = events, automation_service, license_service
    app.state.runtime, app.state.rate_limiter = runtime, SlidingWindowLimiter()
    app.state.started_at = time.monotonic()
    await telegram_service.initialize()
    try:
        await automation_service.recover()
    except Exception as exc:
        logging.getLogger("tele-automation").exception("Automation recovery failed safely", exc_info=exc)
        events.add("ERROR", "automation_recovery_failed", "Automation recovery failed; no job was resumed")
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
    await automation_service.shutdown()
    await telegram_service.disconnect()


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
    telegram = await request.app.state.telegram.status()
    automation = request.app.state.automation.snapshot()
    return {"status": "ok", "service": "tele-automation",
            "telegram": {key: telegram.get(key) for key in ("configured", "connected", "authorized", "state")},
            "automation": {key: automation.get(key) for key in ("state", "running", "paused", "sent_count", "failed_count", "next_send_at", "last_error")},
            "license_backend": "firebase" if request.app.state.licenses._firebase else "local",
            "runtime_backend": request.app.state.runtime.backend,
            "uptime_seconds": round(time.monotonic() - request.app.state.started_at, 1)}


@app.get("/api/ready", tags=["system"])
async def ready(request: Request):
    telegram_ok = (await request.app.state.telegram.status()).get("connected", False)
    return {"ready": bool(telegram_ok and request.app.state.licenses.configured), "telegram": telegram_ok, "licenses": request.app.state.licenses.configured}


@app.get("/api/metrics", tags=["system"])
async def metrics(request: Request):
    snapshot = request.app.state.automation.snapshot()
    return {"uptime_seconds": round(time.monotonic() - request.app.state.started_at, 1), "automation_state": snapshot["state"], "sent": snapshot["sent_count"], "failed": snapshot["failed_count"], "delivery_history": len(request.app.state.runtime.deliveries(500)), "runtime_backend": request.app.state.runtime.backend}


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
                runtime.cleanup_deliveries, DELIVERY_RETENTION_DAYS, DELIVERY_RETENTION_MAX_RECORDS
            )
            if removed:
                events.add("INFO", "delivery_retention_cleanup", f"Removed {removed} expired delivery record(s)")
                events.audit("delivery_retention_cleanup", removed=removed)
        except Exception:
            logging.getLogger("tele-automation").exception("Delivery retention cleanup failed")
        await asyncio.sleep(DELIVERY_RETENTION_INTERVAL_SECONDS)


for route in (settings.router, auth.router, telegram.router, messages.router, automation.router, licenses.router):
    app.include_router(route, prefix="/api")
