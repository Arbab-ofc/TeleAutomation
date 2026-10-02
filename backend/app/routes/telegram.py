from fastapi import APIRouter, Depends, HTTPException, Request
from telethon import errors

from app.models.schemas import MessagePayload
from app.dependencies import get_current_user, require_same_origin
from app.services.app_session_service import AppUser

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.get("/status")
async def status(request: Request, user: AppUser = Depends(get_current_user)):
    return await (await request.app.state.telegram.get(user.id)).status()


@router.get("/dialogs")
async def dialogs(request: Request, user: AppUser = Depends(get_current_user)):
    service = await request.app.state.telegram.get(user.id)
    try:
        return {"items": await service.dialogs()}
    except Exception as exc:
        raise HTTPException(400, service.error_message(exc)) from exc


@router.post("/send-message")
async def send_message(payload: MessagePayload, request: Request, user: AppUser = Depends(get_current_user), _: None = Depends(require_same_origin)):
    service = await request.app.state.telegram.get(user.id)
    events = await request.app.state.automation.events(user.id)
    try:
        dialogs = [await service.resolve_writable_dialog(chat_id) for chat_id in payload.chat_ids]
        sent, failures = 0, []
        for chat_id, dialog in zip(payload.chat_ids, dialogs):
            try:
                await service.send_message(chat_id, payload.message)
                sent += 1
                events.add("SUCCESS", "manual_send", "Message sent once", dialog["title"])
            except (errors.FloodWaitError, errors.SlowModeWaitError):
                raise
            except Exception as exc:
                message = service.error_message(exc)
                failures.append({"chat_id": chat_id, "title": dialog["title"], "error": message})
                events.add("ERROR", "manual_send_failed", message, dialog["title"], message)
        return {"status": "sent" if not failures else "partial", "sent_count": sent, "failed_count": len(failures), "failures": failures}
    except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
        raise HTTPException(429, service.error_message(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, service.error_message(exc)) from exc
