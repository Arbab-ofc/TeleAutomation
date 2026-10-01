from fastapi import APIRouter, HTTPException, Request
from telethon import errors

from app.models.schemas import MessagePayload

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.get("/status")
async def status(request: Request):
    return await request.app.state.telegram.status()


@router.get("/dialogs")
async def dialogs(request: Request):
    try:
        return {"items": await request.app.state.telegram.dialogs()}
    except Exception as exc:
        raise HTTPException(400, request.app.state.telegram.error_message(exc)) from exc


@router.post("/send-message")
async def send_message(payload: MessagePayload, request: Request):
    service = request.app.state.telegram
    try:
        dialogs = [await service.resolve_writable_dialog(chat_id) for chat_id in payload.chat_ids]
        sent, failures = 0, []
        for chat_id, dialog in zip(payload.chat_ids, dialogs):
            try:
                await service.send_message(chat_id, payload.message)
                sent += 1
                request.app.state.events.add("SUCCESS", "manual_send", "Message sent once", dialog["title"])
            except (errors.FloodWaitError, errors.SlowModeWaitError):
                raise
            except Exception as exc:
                message = service.error_message(exc)
                failures.append({"chat_id": chat_id, "title": dialog["title"], "error": message})
                request.app.state.events.add("ERROR", "manual_send_failed", message, dialog["title"], message)
        return {"status": "sent" if not failures else "partial", "sent_count": sent, "failed_count": len(failures), "failures": failures}
    except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
        raise HTTPException(429, service.error_message(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, service.error_message(exc)) from exc
