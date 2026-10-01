from fastapi import APIRouter, HTTPException, Query, Request

from app.models.schemas import AutomationStart

router = APIRouter(prefix="/automation", tags=["automation"])


@router.post("/start")
async def start(payload: AutomationStart, request: Request):
    try:
        license_result = await request.app.state.licenses.validate_for_job(payload.license_key)
        if not license_result:
            raise PermissionError("License key is invalid or expired.")
        return await request.app.state.automation.start(
            payload.chat_ids, payload.message, payload.interval_seconds, license_record_id=license_result["id"],
            start_at=payload.start_at, active_weekdays=payload.active_weekdays,
            window_start=payload.window_start, window_end=payload.window_end,
            timezone_offset_minutes=payload.timezone_offset_minutes,
            max_messages=payload.max_messages,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, LookupError, PermissionError, ConnectionError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, request.app.state.telegram.error_message(exc)) from exc


@router.post("/stop")
async def stop(request: Request):
    return await request.app.state.automation.stop()


@router.post("/pause")
async def pause(request: Request):
    try:
        return await request.app.state.automation.pause()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/resume")
async def resume(request: Request):
    try:
        return await request.app.state.automation.resume()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/status")
async def status(request: Request):
    return request.app.state.automation.snapshot()


@router.get("/logs")
async def logs(request: Request, limit: int = Query(100, ge=1, le=200)):
    return {"items": request.app.state.events.latest(limit)}


@router.get("/deliveries")
async def deliveries(request: Request, limit: int = Query(100, ge=1, le=500)):
    return {"items": request.app.state.runtime.deliveries(limit)}


@router.get("/job")
async def job(request: Request):
    return request.app.state.runtime.job()
