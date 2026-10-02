from fastapi import APIRouter, Cookie, Depends, HTTPException, Request

from app.config import MIN_INTERVAL_SECONDS
from app.dependencies import get_current_user, require_same_origin
from app.models.schemas import SettingsUpdate
from app.services.app_session_service import AppUser

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(request: Request, _: AppUser = Depends(get_current_user)):
    values = await request.app.state.storage.get_settings()
    return {"configured": bool(values.get("api_id") and values.get("api_hash")),
            "api_id": values.get("api_id"), "api_hash": None,
            "minimum_interval_seconds": MIN_INTERVAL_SECONDS}


@router.put("")
async def update_settings(payload: SettingsUpdate, request: Request, _: AppUser = Depends(get_current_user),
                          __: None = Depends(require_same_origin), admin_session: str | None = Cookie(default=None)):
    if not request.app.state.licenses.verify_admin_token(admin_session or ""):
        raise HTTPException(401, "Admin authorization required to change application Telegram credentials.")
    if request.app.state.automation.active_count:
        raise HTTPException(409, "Stop all automations before updating application Telegram credentials.")
    await request.app.state.storage.save_settings(payload.api_id, payload.api_hash)
    try:
        await request.app.state.telegram.rebuild_all()
    except Exception as exc:
        raise HTTPException(400, "Telegram could not apply the application credentials.") from exc
    return {"configured": True, "api_id": payload.api_id, "api_hash": None,
            "minimum_interval_seconds": MIN_INTERVAL_SECONDS}
