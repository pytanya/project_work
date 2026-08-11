"""Документы: upload + список проиндексированных материалов (раздел 8.1)."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Response, UploadFile

from src.config import settings as default_settings
from src.export import questions_csv

from ..deps import get_session, get_store
from ..engine import SessionStore, run_step

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["documents"])


def _uploads_dir() -> Path:
    d = Path(default_settings.CHROMA_PERSIST_DIR).parent / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/upload")
async def upload_document(
    session_id: str,
    file: UploadFile = File(...),
    store: SessionStore = Depends(get_store),
):
    session = get_session(store, session_id)
    suffix = Path(file.filename or "doc").suffix.lower()
    if suffix not in (".pdf", ".docx", ".txt"):
        return {"ok": False, "error": "Поддерживаются только PDF/DOCX/TXT"}
    dest = _uploads_dir() / f"{uuid.uuid4().hex[:8]}{suffix}"
    dest.write_bytes(await file.read())

    # Upload отменяет веб-поиск/веб-источники: файл становится источником №1
    session.state = session.state.model_copy(
        update={
            "textbook_file": str(dest),
            "has_textbook": True,
            "sources": [],
            "collection_id": None,
            "source_status": None,
            "source_note": None,
        }
    )
    await run_step(session)
    st = session.state
    return {
        "ok": True,
        "filename": file.filename,
        "status": st.source_status,
        "note": st.source_note,
        "scanned": st.textbook_scanned,
        "num_chunks": st.sources[0].get("num_chunks") if st.sources else None,
        "next_question": st.agent_question or "",
    }


@router.get("/knowledge")
def list_knowledge(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return {
        "sources": session.state.sources,
        "collection_id": session.state.collection_id,
        "note": session.state.source_note,
    }


@router.get("/export")
def export_session(session_id: str, store: SessionStore = Depends(get_store)):
    """Экспорт для учителя: CSV вопросов сессии (расширение 15.1 п.7)."""
    session = get_session(store, session_id)
    csv_text = questions_csv(session.state.records, session_id)
    headers = {
        "Content-Disposition": f'attachment; filename="{session_id}_questions.csv"'
    }
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )
