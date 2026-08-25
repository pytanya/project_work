"""Ученики: профили и история занятий (раздел 8.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import get_store
from ..engine import SessionStore

router = APIRouter(prefix="/api/students", tags=["students"])


@router.get("/{student_id}/sessions")
def student_sessions(
    student_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    store: SessionStore = Depends(get_store),
):
    """Последние занятия ученика (дата, предмет/тема, режим, счёт квиза)."""
    return {"sessions": store.student_store.list_sessions(student_id, limit=limit)}
