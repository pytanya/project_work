"""Knowledge Wiki (roadmap #2): GET /api/wiki — накопленная база знаний ученика."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.config import settings as default_settings
from src.wiki import KnowledgeWiki

from ..deps import get_store
from ..engine import SessionStore

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


def _wiki() -> KnowledgeWiki:
    return KnowledgeWiki(default_settings.KNOWLEDGE_WIKI_DIR)


class WikiEnrichBody(BaseModel):
    subject: str
    topic: str


@router.post("/enrich")
def wiki_enrich(body: WikiEnrichBody, store: SessionStore = Depends(get_store)):
    """Сгенерировать изложение темы (LLM-wiki) по требованию.

    НЕ зависит от сессии: векторный стор общий (_base_deps), поэтому устаревший
    session id фронтенда не приводит к 404. Best-effort: LLM недоступна или контекста
    нет → статья как есть + note с объяснением.
    """
    from src.graph import _rag_chunks
    from src.llm_client import LLMClient
    from src.states import TutorState

    base = getattr(store, "_base_deps", None)
    if base is None:
        return {"article": None, "note": "Хранилище недоступно."}
    st = TutorState(subject=body.subject)
    chunks = _rag_chunks(base.store, body.topic, st, k=4)
    context = [c.chunk.text for c in chunks]
    wiki = _wiki()
    subject = body.subject or "общая тема"
    art = wiki.get(subject, body.topic)
    if not context:
        return {
            "article": art.to_dict() if art else None,
            "note": "Нет материалов по теме в индексе — загрузите учебник или найдите источник, затем повторите.",
        }

    if callable(getattr(base, "tutor_llm", None)):
        llm_call = base.tutor_llm  # инъекция (тесты)
    else:
        client = LLMClient(role="tutor")
        llm_call = lambda msgs: client.chat(msgs, temperature=0.3, max_tokens=500).content or ""
    art = wiki.enrich_body(st, body.topic, context, llm_call=llm_call)
    src = next((c.chunk.source for c in chunks if c.chunk.source), "")
    if src:
        wiki.set_source(st, body.topic, src)
        art = wiki.get(subject, body.topic) or art  # перечитать после set_source
    return {"article": art.to_dict() if art else None, "note": ""}


@router.get("")
def wiki_summary(_store: SessionStore = Depends(get_store)):
    """Сводка базы знаний: предмет → темы с мастерством/попытками (между сессиями)."""
    return {"subjects": _wiki().to_summary_dict()}


@router.get("/{subject}")
def wiki_subject(subject: str, _store: SessionStore = Depends(get_store)):
    """Статьи по предмету."""
    wiki = _wiki()
    articles = [a.to_dict() for a in wiki.list_articles(subject)]
    if not articles:
        raise HTTPException(status_code=404, detail="Предмет не найден в базе знаний")
    return {"subject": subject, "articles": articles}


@router.get("/{subject}/{topic}")
def wiki_article(subject: str, topic: str, _store: SessionStore = Depends(get_store)):
    """Одна wiki-статья темы."""
    art = _wiki().get(subject, topic)
    if art is None:
        raise HTTPException(status_code=404, detail="Тема не найдена в базе знаний")
    return art.to_dict()
