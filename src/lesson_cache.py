"""Кэш сгенерированных уроков по ключу (student_id, subject, topic, grade).

При повторном прохождении темы ученик получает мгновенно закэшированный урок,
а не ждёт повторную генерацию LLM (план доработки, блоки 3 и 7).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from api.schemas import Lesson


def _cache_key(student_id: str, subject: str, topic: str, grade: str) -> str:
    """SHA-256 ключ по (student_id, subject, topic, grade), нижний регистр."""
    raw = f"{student_id}::{subject}::{topic}::{grade}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _lessons_dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / "lessons"


def save_lesson(
    cache_dir: Path,
    student_id: str,
    subject: str,
    topic: str,
    grade: str,
    lesson: Lesson,
) -> Optional[Path]:
    """Сохраняет урок в кэш. Возвращает путь или None при ошибке."""
    try:
        lessons_dir = _lessons_dir(cache_dir)
        lessons_dir.mkdir(parents=True, exist_ok=True)
        key = _cache_key(student_id, subject, topic, grade)
        path = lessons_dir / f"lesson_{key}.json"
        path.write_text(lesson.model_dump_json(indent=2), encoding="utf-8")
        return path
    except Exception:
        return None


def load_lesson(
    cache_dir: Path,
    student_id: str,
    subject: str,
    topic: str,
    grade: str,
) -> Optional[Lesson]:
    """Загружает урок из кэша по ключу. None — нет кэша / битый JSON."""
    if not topic:
        return None
    key = _cache_key(student_id, subject, topic, grade)
    path = _lessons_dir(cache_dir) / f"lesson_{key}.json"
    if not path.exists():
        return None
    try:
        return Lesson.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None
