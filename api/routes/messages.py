"""Взаимодействие: message / cancel / history / WebSocket (раздел 8.1, 8.3)."""

from __future__ import annotations

import asyncio
import queue as std_queue

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from api.schemas import MessageResponse, WsEvent

from ..deps import get_session, get_store, get_store_ws
from ..engine import SessionStore, WS_IDLE_TIMEOUT_SEC, message_response, run_step

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


# Короткий поллинг очереди (в to_thread — не блокируем event loop):
# чтобы не закрывать WS по idle во время долгого шага графа.
_WS_POLL_SEC = 5.0


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
    idle = 0.0
    try:
        while True:
            try:
                event = await asyncio.to_thread(session.queue.get, timeout=_WS_POLL_SEC)
            except std_queue.Empty:
                # Закрываем только при длительном простое БЕЗ активного шага графа
                if session.step_active:
                    idle = 0.0
                    continue
                idle += _WS_POLL_SEC
                if idle >= WS_IDLE_TIMEOUT_SEC:
                    await websocket.close(code=1000, reason="idle timeout")
                    break
                continue
            idle = 0.0
            await websocket.send_json(event.model_dump())
    except WebSocketDisconnect:
        return
