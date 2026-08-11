"""Взаимодействие: message / cancel / history / WebSocket (раздел 8.1, 8.3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from api.schemas import MessageResponse, WsEvent

from ..deps import get_session, get_store, get_store_ws
from ..engine import SessionStore, WS_IDLE_TIMEOUT_SEC, get_next_event, message_response, run_step

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["messages"])


class MessageBody(BaseModel):
    text: str


@router.post("/message", response_model=MessageResponse)
async def post_message(session_id: str, body: MessageBody, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    session.history.append({"role": "user", "text": body.text})
    await run_step(session, answer=body.text)
    return message_response(session.state)


@router.post("/cancel")
def cancel_task(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    session.cancel_event.set()
    return {"status": "cancelled", "session_id": session_id}


@router.get("/history")
def message_history(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return {"history": session.history}


@router.websocket("/ws")
async def ws_stream(websocket: WebSocket, session_id: str, store: SessionStore = Depends(get_store_ws)):
    await websocket.accept()
    session = store.get(session_id)
    if session is None:
        await websocket.send_json(
            WsEvent(event="session.error", data={"code": "not_found", "message": "Сессия не найдена"}).model_dump()
        )
        await websocket.close(code=4004)
        return
    try:
        while True:
            event = await get_next_event(session, timeout=WS_IDLE_TIMEOUT_SEC)
            if event is None:
                await websocket.close(code=1000, reason="idle timeout")
                break
            await websocket.send_json(event.model_dump())
    except WebSocketDisconnect:
        return
