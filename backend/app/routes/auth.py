from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from app.config import APP_SESSION_COOKIE
from app.dependencies import get_current_user, require_same_origin, set_app_session_cookie
from app.models.schemas import CodeRequest, PasswordRequest, PhoneRequest
from app.services.app_session_service import AppUser

router = APIRouter(prefix="/telegram", tags=["telegram-auth"])


def fail(service, exc: Exception, status: int = 400):
    raise HTTPException(status, service.error_message(exc)) from exc


@router.post("/send-code")
async def send_code(payload: PhoneRequest, request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    service = await request.app.state.telegram.get(user.id)
    allowed, retry = request.app.state.rate_limiter.allow(f"telegram-code:{user.id}", 5, 300)
    if not allowed:
        raise HTTPException(429, f"Too many requests. Retry in {retry}s.", headers={"Retry-After": str(retry)})
    try:
        await service.send_code(payload.phone)
        return {"status": "CODE_SENT"}
    except Exception as exc:
        fail(service, exc)


@router.post("/verify-code")
async def verify_code(payload: CodeRequest, request: Request, response: Response,
                      user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin),
                      token: str | None = Cookie(default=None, alias=APP_SESSION_COOKIE)):
    service = await request.app.state.telegram.get(user.id)
    try:
        status = await service.verify_code(payload.code)
        if status == "AUTHORIZED" and token:
            set_app_session_cookie(response, request.app.state.app_sessions.rotate(token, user.id))
        return {"status": status}
    except Exception as exc:
        fail(service, exc)


@router.post("/verify-password")
async def verify_password(payload: PasswordRequest, request: Request, response: Response,
                          user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin),
                          token: str | None = Cookie(default=None, alias=APP_SESSION_COOKIE)):
    service = await request.app.state.telegram.get(user.id)
    try:
        await service.verify_password(payload.password)
        if token:
            set_app_session_cookie(response, request.app.state.app_sessions.rotate(token, user.id))
        return {"status": "AUTHORIZED"}
    except Exception as exc:
        fail(service, exc)


@router.post("/reconnect")
async def reconnect(request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    service = await request.app.state.telegram.get(user.id)
    try:
        await service.reconnect()
        return await service.status()
    except Exception as exc:
        fail(service, exc)


@router.post("/logout")
async def logout(request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    await request.app.state.automation.stop(user.id)
    service = await request.app.state.telegram.get(user.id)
    await service.logout()
    await request.app.state.telegram.disconnect(user.id, remove=True)
    return {"status": "logged_out"}


@router.post("/disconnect")
async def disconnect(request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    await request.app.state.automation.stop(user.id)
    await request.app.state.telegram.disconnect(user.id, remove=True)
    return {"status": "disconnected"}
