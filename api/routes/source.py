"""Поиск источника: find-textbook + статус (раздел 8.1)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_session, get_store
from ..engine import SessionStore, run_step

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["source"])


class FindTextbookBody(BaseModel):
    subject: Optional[str] = None
    grade: Optional[str] = None
    author: Optional[str] = None


@router.post("/find-textbook")
async def find_textbook(session_id: str, body: Optional[FindTextbookBody] = None, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    body = body or FindTextbookBody()
    update = {"has_textbook": False, "textbook_file": None, "sources": [], "source_status": None,
              "collection_id": None, "knowledge_graph": None, "active_topic": None,
              "force_source_refresh": True}
    if body.subject:
        update["subject"] = body.subject
    if body.grade:
        update["grade"] = body.grade
    if body.author:
        update["textbook_author"] = body.author
    session.state = session.state.model_copy(update=update)

    await run_step(session)
    return {
        "status": session.state.source_status,
        "note": session.state.source_note,
        "sources": session.state.sources,
    }


@router.get("/source-status")
def source_status(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return {
        "status": session.state.source_status,
        "note": session.state.source_note,
        "sources": session.state.sources,
    }
