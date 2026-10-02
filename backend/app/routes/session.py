from fastapi import APIRouter, Cookie, Depends, Request, Response

from app.config import APP_SESSION_COOKIE, COOKIE_SECURE
from app.dependencies import get_current_user, require_same_origin
from app.services.app_session_service import AppUser

router = APIRouter(prefix="/me", tags=["application-session"])


@router.get("/status")
async def status(request: Request, user: AppUser = Depends(get_current_user)):
    telegram = await (await request.app.state.telegram.get(user.id)).status()
    automation = (await request.app.state.automation.get(user.id)).snapshot()
    return {"user_id": user.id, "telegram": telegram, "automation": automation}


@router.post("/logout")
async def logout(request: Request, response: Response, _: None = Depends(require_same_origin),
                 token: str | None = Cookie(default=None, alias=APP_SESSION_COOKIE)):
    request.app.state.app_sessions.invalidate(token)
    response.delete_cookie(APP_SESSION_COOKIE, path="/api", httponly=True,
                           secure=COOKIE_SECURE, samesite="lax")
    return {"logged_out": True}
