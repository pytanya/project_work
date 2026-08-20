"""Knowledge Wiki (roadmap #2): GET /api/wiki — накопленная база знаний ученика."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.config import settings as default_settings
from src.wiki import KnowledgeWiki

from ..deps import get_store
from ..engine import SessionStore

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


def _wiki() -> KnowledgeWiki:
    return KnowledgeWiki(default_settings.KNOWLEDGE_WIKI_DIR)


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
