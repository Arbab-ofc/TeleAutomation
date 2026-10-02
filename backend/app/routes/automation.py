from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import get_current_user, require_same_origin
from app.models.schemas import AutomationStart
from app.services.app_session_service import AppUser

router = APIRouter(prefix="/automation", tags=["automation"])


@router.post("/start")
async def start(payload: AutomationStart, request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    automation = await request.app.state.automation.get(user.id)
    telegram = await request.app.state.telegram.get(user.id)
    try:
        license_result = await request.app.state.licenses.validate_for_job(payload.license_key, user.id)
        if not license_result:
            raise PermissionError("License key is invalid or expired.")
        return await automation.start(
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
        raise HTTPException(400, telegram.error_message(exc)) from exc


@router.post("/stop")
async def stop(request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    return await request.app.state.automation.stop(user.id)


@router.post("/pause")
async def pause(request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    try:
        return await (await request.app.state.automation.get(user.id)).pause()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/resume")
async def resume(request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    try:
        return await (await request.app.state.automation.get(user.id)).resume()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/status")
async def status(request: Request, user: AppUser = Depends(get_current_user)):
    return (await request.app.state.automation.get(user.id)).snapshot()


@router.get("/logs")
async def logs(request: Request, limit: int = Query(100, ge=1, le=200), user: AppUser = Depends(get_current_user)):
    return {"items": (await request.app.state.automation.events(user.id)).latest(limit)}


@router.get("/deliveries")
async def deliveries(request: Request, limit: int = Query(100, ge=1, le=500), user: AppUser = Depends(get_current_user)):
    return {"items": request.app.state.runtime.deliveries(user.id, limit)}


@router.get("/job")
async def job(request: Request, user: AppUser = Depends(get_current_user)):
    return request.app.state.runtime.job(user.id)
