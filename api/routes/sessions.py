"""Сессии: CRUD (раздел 8.1)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from ..deps import get_session, get_store
from ..engine import SessionStore, run_step

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionBody(BaseModel):
    initial: Optional[Dict[str, Any]] = None
    # Стабильный ID ученика (из localStorage фронта). Если не задан — создаётся новый
    # профиль, а его ID возвращается клиенту для сохранения на устройстве.
    student_id: Optional[str] = None


@router.post("", status_code=201)
async def create_session(body: Optional[CreateSessionBody] = None, store: SessionStore = Depends(get_store)):
    b = body or CreateSessionBody()
    initial = b.initial or {}

    # Профиль ученика: по ID из запроса (вернувшийся) или новый (знакомство).
    # Префилл стабильных полей (имя/тип/класс) — intake становится короче.
    if b.student_id:
        profile = store.student_store.get(b.student_id)
        if profile is None:
            profile = store.student_store.get_or_create(b.student_id)
    else:
        profile = store.student_store.get_or_create(f"stu_{uuid.uuid4().hex[:10]}")
    for k, v in profile.prefill().items():
        initial.setdefault(k, v)

    # Новая сессия — каждый POST создаёт свежую (для «Новая сессия»/открытия приложения)
    session = store.create(initial)
    # Первый шаг графа в фоне: session_id возвращаем сразу, первый вопрос
    # чек-листа придёт через WS (intake.card / intake.question). Не блокируем создание.
    asyncio.create_task(run_step(session))
    return {
        "session_id": session.id,
        "student_id": profile.student_id,
        "student_name": profile.name,
    }


@router.get("/{session_id}")
def get_session_state(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return session.state.model_dump()


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str, store: SessionStore = Depends(get_store)):
    if not store.delete(session_id):
        return Response(status_code=404)
    return Response(status_code=204)
