from fastapi import Cookie, HTTPException, Request, Response

from app.config import APP_SESSION_COOKIE, APP_SESSION_TTL_SECONDS, COOKIE_SECURE, CORS_ORIGINS
from app.services.app_session_service import AppUser


def set_app_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        APP_SESSION_COOKIE, token, max_age=APP_SESSION_TTL_SECONDS,
        httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/api",
    )


async def get_current_user(
    request: Request,
    response: Response,
    teleautomation_session: str | None = Cookie(default=None),
) -> AppUser:
    sessions = request.app.state.app_sessions
    user = sessions.resolve(teleautomation_session)
    if user:
        return user
    user, token = sessions.create()
    set_app_session_cookie(response, token)
    return user


async def require_same_origin(request: Request) -> None:
    """Reject cross-origin browser mutations; SameSite=Lax is the second CSRF layer."""
    origin = request.headers.get("origin")
    if not origin:
        return
    own_origin = str(request.base_url).rstrip("/")
    allowed = {value.rstrip("/") for value in CORS_ORIGINS} | {own_origin}
    if origin.rstrip("/") not in allowed:
        raise HTTPException(403, "Cross-origin request rejected.")
