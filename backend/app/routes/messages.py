from fastapi import APIRouter, HTTPException, Request, Response, status

from app.models.schemas import TemplateCreate, TemplateUpdate

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("")
async def list_messages(request: Request):
    return {"items": await request.app.state.storage.list_messages()}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_message(payload: TemplateCreate, request: Request):
    try:
        return await request.app.state.storage.create_message(payload.name, payload.content)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/{item_id}")
async def update_message(item_id: str, payload: TemplateUpdate, request: Request):
    try:
        return await request.app.state.storage.update_message(item_id, payload.name, payload.content)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(item_id: str, request: Request):
    try:
        await request.app.state.storage.delete_message(item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from exc
