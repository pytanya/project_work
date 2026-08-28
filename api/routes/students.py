"""Ученики: профили, история занятий и политика источников (раздел 8.5)."""

from __future__ import annotations

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..deps import get_store
from ..engine import SessionStore

router = APIRouter(prefix="/api/students", tags=["students"])


@router.get("/{student_id}/sessions")
def student_sessions(
    student_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    subject: Optional[str] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    store: SessionStore = Depends(get_store),
):
    """Последние занятия ученика (дата, предмет/тема, режим, счёт квиза)."""
    return {
        "sessions": store.student_store.list_sessions(
            student_id, limit=limit, subject=subject, mode=mode,
        )
    }


_REGIONAL_PREFIXES = {
    "ru", "en", "de", "fr", "es", "it", "pt", "uk", "by", "kz", "uz", "md",
    "ua", "pl", "cz", "bg", "ro", "hu", "fi", "se", "no", "dk", "nl", "be",
    "at", "ch", "gr", "tr", "cn", "jp", "kr", "in", "br", "mx", "ar", "co",
    "m", "mobile", "w", "l", "class", "edu", "school",
}


def _normalize_domain(raw: str) -> str:
    """Домен из произвольной строки: scheme/пути/пробелы убираются, регистр — нижний.

    Региональные/служебные префиксы (ru., www., m., en., edu.) отбрасываются —
    «ru.wikibooks.org» сводится к «wikibooks.org», а «lc.rt.ru» остаётся как есть.
    """
    d = raw.strip().lower()
    d = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", d)   # https://, ftp://
    d = re.sub(r"^www\.", "", d)
    d = d.split("/")[0].split("?")[0].split("#")[0]  # пути/query/fragment
    d = d.strip()
    if not d or " " in d or "." not in d:
        return ""
    parts = d.split(".")
    if len(parts) >= 3 and parts[0] in _REGIONAL_PREFIXES:
        parts = parts[1:]
    d = ".".join(parts)
    if not d or "." not in d:
        return ""
    return d


class SourcePolicyBody(BaseModel):
    allow_any_sources: Optional[bool] = None
    whitelist: Optional[List[str]] = None


@router.get("/{student_id}/sources")
def get_source_policy(student_id: str, store: SessionStore = Depends(get_store)):
    """Политика источников ученика: флаг «любые источники» + белый список доменов."""
    profile = store.student_store.get(student_id)
    if profile is None:
        return {"allow_any_sources": True, "whitelist": []}
    return {
        "allow_any_sources": bool(profile.allow_any_sources),
        "whitelist": list(profile.source_whitelist),
    }


@router.put("/{student_id}/sources")
def put_source_policy(student_id: str, body: SourcePolicyBody, store: SessionStore = Depends(get_store)):
    """Обновить политику источников: нормализация доменов, сохранение в профиль
    и синхронизация активных сессий ученика (чтобы следующий поиск шёл уже по новой политике)."""
    profile = store.student_store.get_or_create(student_id)
    if body.allow_any_sources is not None:
        profile.allow_any_sources = bool(body.allow_any_sources)
    if body.whitelist is not None:
        profile.source_whitelist = [d for d in map(_normalize_domain, body.whitelist) if d]
    store.student_store.save(profile)
    store.apply_source_policy_to_sessions(student_id, profile.allow_any_sources, profile.source_whitelist)
    return {
        "allow_any_sources": bool(profile.allow_any_sources),
        "whitelist": list(profile.source_whitelist),
    }
