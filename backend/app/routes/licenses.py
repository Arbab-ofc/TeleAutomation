import json

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import ValidationError

from app.models.schemas import AdminContact, AdminVerify, LicenseCreate, LicenseValidate
from app.config import COOKIE_SECURE
from app.dependencies import get_current_user, require_same_origin
from app.services.app_session_service import AppUser

router = APIRouter(prefix="/licenses", tags=["licenses"])


def _admin(request: Request, authorization: str | None, admin_session: str | None = None) -> None:
    token = (admin_session or authorization or "").removeprefix("Bearer ").strip()
    if not request.app.state.licenses.verify_admin_token(token):
        raise HTTPException(401, "Admin authorization required.")


def _limit(request: Request, key: str, limit: int = 30) -> None:
    allowed, retry = request.app.state.rate_limiter.allow(f"{key}:{request.client.host if request.client else 'unknown'}", limit, 60)
    if not allowed:
        raise HTTPException(429, f"Too many requests. Retry in {retry}s.", headers={"Retry-After": str(retry)})


@router.post("/admin/verify")
async def verify_admin(payload: AdminVerify, request: Request, response: Response, _: None = Depends(require_same_origin)):
    _limit(request, "admin-verify", 5)
    token = request.app.state.licenses.verify_admin_code(payload.code)
    if not token:
        request.app.state.events.audit("admin_login_failed", ip=request.client.host if request.client else None)
        raise HTTPException(401, "Invalid admin code.")
    response.set_cookie(
        "admin_session", token, max_age=3600, httponly=True,
        secure=COOKIE_SECURE, samesite="lax", path="/",
    )
    request.app.state.events.audit("admin_login", ip=request.client.host if request.client else None)
    return {"authenticated": True}


@router.post("/admin/generate")
async def generate(request: Request, authorization: str | None = Header(default=None), admin_session: str | None = Cookie(default=None), _: None = Depends(require_same_origin)):
    _limit(request, "admin-generate")
    _admin(request, authorization, admin_session)
    try:
        raw = await request.json()
        # Accept both the normal JSON object and a legacy double-encoded JSON body.
        if isinstance(raw, str):
            raw = json.loads(raw)
        payload = LicenseCreate.model_validate(raw)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        detail = exc.errors() if isinstance(exc, ValidationError) else "License request must be a JSON object."
        raise HTTPException(422, detail=detail) from exc
    result = await request.app.state.licenses.generate(payload.duration_value, payload.duration_unit, payload.start_date, payload.end_date)
    request.app.state.events.audit("license_generated", record_id=result.get("id"), expires_at=result.get("expires_at"))
    return result


@router.get("/admin/list")
async def list_licenses(request: Request, authorization: str | None = Header(default=None), admin_session: str | None = Cookie(default=None)):
    _limit(request, "admin-list")
    _admin(request, authorization, admin_session)
    return {"items": await request.app.state.licenses.list()}


@router.delete("/admin/{record_id}")
async def revoke(record_id: str, request: Request, authorization: str | None = Header(default=None), admin_session: str | None = Cookie(default=None), _: None = Depends(require_same_origin)):
    _limit(request, "admin-revoke")
    _admin(request, authorization, admin_session)
    if not await request.app.state.licenses.revoke(record_id):
        raise HTTPException(404, "License not found.")
    request.app.state.events.audit("license_revoked", record_id=record_id)
    return {"revoked": True}


@router.get("/admin/audit")
async def audit(request: Request, limit: int = 100, authorization: str | None = Header(default=None), admin_session: str | None = Cookie(default=None)):
    _limit(request, "admin-audit")
    _admin(request, authorization, admin_session)
    return {"items": request.app.state.events.audit_latest(limit)}


@router.post("/validate")
async def validate(payload: LicenseValidate, request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    _limit(request, "license-validate", 30)
    result = await request.app.state.licenses.validate(payload.key, user.id)
    if not result:
        raise HTTPException(403, "License key is invalid or expired.")
    return result


@router.post("/admin/logout")
async def logout(response: Response, _: None = Depends(require_same_origin)):
    response.delete_cookie(
        "admin_session", path="/", httponly=True,
        secure=COOKIE_SECURE, samesite="lax",
    )
    return {"logged_out": True}


@router.get("/contact")
async def contact(request: Request):
    return {"telegram_username": request.app.state.licenses.get_admin_contact()}


@router.put("/admin/contact")
async def update_contact(payload: AdminContact, request: Request, authorization: str | None = Header(default=None), admin_session: str | None = Cookie(default=None), _: None = Depends(require_same_origin)):
    _limit(request, "admin-contact")
    _admin(request, authorization, admin_session)
    username = request.app.state.licenses.set_admin_contact(payload.telegram_username)
    request.app.state.events.audit("admin_contact_updated", telegram_username=username)
    return {"telegram_username": username}
