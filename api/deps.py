"""Общие зависимости API (раздел 8.4)."""

from __future__ import annotations

from fastapi import HTTPException, Request, WebSocket

from .engine import SessionStore


def get_store(request: Request) -> SessionStore:
    return request.app.state.store


def get_store_ws(websocket: WebSocket) -> SessionStore:
    return websocket.app.state.store


def get_session(store: SessionStore, session_id: str):
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return session
