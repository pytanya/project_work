"""Сессии: CRUD (раздел 8.1)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from ..deps import get_session, get_store
from ..engine import SessionStore, run_step

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionBody(BaseModel):
    initial: Optional[Dict[str, Any]] = None


@router.post("", status_code=201)
async def create_session(body: Optional[CreateSessionBody] = None, store: SessionStore = Depends(get_store)):
    initial = (body or CreateSessionBody()).initial or {}
    session = store.create(initial)
    # Первый шаг графа: чек-лист спрашивает первый вопрос (иначе next_question пуст)
    await run_step(session)
    return {"session_id": session.id}


@router.get("/{session_id}")
def get_session_state(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return session.state.model_dump()


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str, store: SessionStore = Depends(get_store)):
    if not store.delete(session_id):
        return Response(status_code=404)
    return Response(status_code=204)
