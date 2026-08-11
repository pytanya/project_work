"""Документы: upload + список проиндексированных материалов (раздел 8.1)."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile

from src.config import settings as default_settings

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

    session.state = session.state.model_copy(
        update={"textbook_file": str(dest), "has_textbook": True}
    )
    await run_step(session)
    return {
        "ok": True,
        "filename": file.filename,
        "status": session.state.source_status,
        "note": session.state.source_note,
        "num_chunks": session.state.sources[0].get("num_chunks") if session.state.sources else None,
    }


@router.get("/knowledge")
def list_knowledge(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return {
        "sources": session.state.sources,
        "collection_id": session.state.collection_id,
        "note": session.state.source_note,
    }
