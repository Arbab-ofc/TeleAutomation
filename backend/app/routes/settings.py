from fastapi import APIRouter, HTTPException, Request

from app.config import MIN_INTERVAL_SECONDS
from app.models.schemas import SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(request: Request):
    values = await request.app.state.storage.get_settings()
    return {"configured": bool(values.get("api_id") and values.get("api_hash")),
            "api_id": values.get("api_id"), "api_hash": None,
            "minimum_interval_seconds": MIN_INTERVAL_SECONDS}


@router.put("")
async def update_settings(payload: SettingsUpdate, request: Request):
    if request.app.state.automation.running:
        raise HTTPException(409, "Stop automation before updating Telegram credentials.")
    await request.app.state.storage.save_settings(payload.api_id, payload.api_hash)
    try:
        await request.app.state.telegram.rebuild_client()
    except Exception as exc:
        raise HTTPException(400, request.app.state.telegram.error_message(exc)) from exc
    return {"configured": True, "api_id": payload.api_id, "api_hash": None,
            "minimum_interval_seconds": MIN_INTERVAL_SECONDS}
