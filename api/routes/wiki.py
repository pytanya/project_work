"""Knowledge Wiki (roadmap #2): GET /api/wiki — накопленная база знаний ученика.

База персональная: `?student_id=` определяет namespace (данные каждого ученика
в `knowledge_wiki/<student_id>/`). Без student_id — общий/legacy каталог.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.config import settings as default_settings
from src.wiki import KnowledgeWiki

from ..deps import get_store
from ..engine import SessionStore

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


def _wiki_check(student_id: str, store: SessionStore = Depends(get_store)) -> None:
    """Базовая проверка доступа к wiki данных."""
    if student_id and not store.check_student_access(student_id):
        raise HTTPException(status_code=403, detail="Доступ запрещён: нет активной сессии для этого ученика")


def _wiki(student_id: str = "") -> KnowledgeWiki:
    return KnowledgeWiki(default_settings.KNOWLEDGE_WIKI_DIR, student_id=student_id)


class WikiEnrichBody(BaseModel):
    subject: str
    topic: str
    student_id: str = ""


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
    wiki = _wiki(body.student_id)
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
def wiki_summary(student_id: str = Query(default="", description="персональный namespace ученика"),
                  _store: SessionStore = Depends(get_store)):
    """Сводка базы знаний ученика: предмет → темы с мастерством/попытками."""
    _wiki_check(student_id, _store)
    return {"subjects": _wiki(student_id).to_summary_dict()}


@router.get("/{subject}")
def wiki_subject(subject: str, student_id: str = Query(default=""),
                  _store: SessionStore = Depends(get_store)):
    """Статьи по предмету (персонально для ученика)."""
    _wiki_check(student_id, _store)
    wiki = _wiki(student_id)
    articles = [a.to_dict() for a in wiki.list_articles(subject)]
    if not articles:
        raise HTTPException(status_code=404, detail="Предмет не найден в базе знаний")
    return {"subject": subject, "articles": articles}


@router.get("/{subject}/{topic}")
def wiki_article(subject: str, topic: str, student_id: str = Query(default=""),
                  _store: SessionStore = Depends(get_store)):
    """Одна wiki-статья темы (персонально для ученика)."""
    _wiki_check(student_id, _store)
    art = _wiki(student_id).get(subject, topic)
    if art is None:
        raise HTTPException(status_code=404, detail="Тема не найдена в базе знаний")
    return art.to_dict()


@router.delete("/{subject}/{topic}")
def wiki_delete(subject: str, topic: str, student_id: str = Query(default=""),
                _store: SessionStore = Depends(get_store)):
    """Удалить wiki-статью темы (персонально для ученика).

    Изолированно: student_id определяет namespace, subject/topic — конкретную
    статью; удаляется только её файл + обновляется индекс предмета. Используется
    для очистки мусорных карточек от веб-скрапинга (домены, «Мощность. единицы
    измерения» и т.п.), которые не относятся к реальной теме.
    """
    deleted = _wiki(student_id).delete(subject, topic)
    if not deleted:
        raise HTTPException(status_code=404, detail="Тема не найдена в базе знаний")
    return {"deleted": True, "subject": subject, "topic": topic}
