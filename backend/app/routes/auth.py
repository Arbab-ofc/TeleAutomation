from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import CodeRequest, PasswordRequest, PhoneRequest

router = APIRouter(prefix="/telegram", tags=["telegram-auth"])


def fail(service, exc: Exception, status: int = 400):
    raise HTTPException(status, service.error_message(exc)) from exc


@router.post("/send-code")
async def send_code(payload: PhoneRequest, request: Request):
    service = request.app.state.telegram
    try:
        await service.send_code(payload.phone)
        return {"status": "CODE_SENT"}
    except Exception as exc:
        fail(service, exc)


@router.post("/verify-code")
async def verify_code(payload: CodeRequest, request: Request):
    service = request.app.state.telegram
    try:
        return {"status": await service.verify_code(payload.code)}
    except Exception as exc:
        fail(service, exc)


@router.post("/verify-password")
async def verify_password(payload: PasswordRequest, request: Request):
    service = request.app.state.telegram
    try:
        await service.verify_password(payload.password)
        return {"status": "AUTHORIZED"}
    except Exception as exc:
        fail(service, exc)


@router.post("/reconnect")
async def reconnect(request: Request):
    service = request.app.state.telegram
    try:
        await service.reconnect()
        return await service.status()
    except Exception as exc:
        fail(service, exc)


@router.post("/logout")
async def logout(request: Request):
    await request.app.state.automation.stop()
    await request.app.state.telegram.logout()
    return {"status": "logged_out"}
