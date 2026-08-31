"""EduTutor — интервальное повторение (SM-2 Question Bank, roadmap).

Карточки ошибочных вопросов копятся в `data/review_bank/<student_id>.json`.
При верном повторе интервал растёт (репетиции → дни); при срыве — интервал сбрасывается.
Дедуп по хэшу текста вопроса; битый файл банка → пустой банк (fail-soft).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

logger = logging.getLogger("edututor.review")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _parse_iso(value: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def card_id_for(question: str) -> str:
    """Стабильный ID карточки по тексту вопроса."""
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()[:16]


class ReviewCard(BaseModel):
    card_id: str
    student_id: str
    subject: str = ""
    topic: str = ""
    question: str
    options: Optional[List[str]] = None
    answer_type: str = "open"
    correct_answer: str = ""
    difficulty: str = "medium"
    added_at: str = ""
    last_reviewed: str = ""
    due_at: str = ""
    interval_days: float = 1.0
    ease: float = 2.5
    reps: int = 0
    lapses: int = 0

    @property
    def is_due(self) -> bool:
        if not self.due_at:
            return True
        due = _parse_iso(self.due_at)
        return due is None or due <= datetime.datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ReviewBank:
    """Пер-студентный банк карточек (JSON по файлу)."""

    def __init__(self, root_dir: Any, student_id: str, max_cards: int = 200) -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.student_id = student_id
        self.max_cards = int(max_cards or 200)

    def _path(self) -> Path:
        return self.root / f"{self.student_id}.json"

    def _load(self) -> List[ReviewCard]:
        p = self._path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return [ReviewCard.model_validate(x) for x in data if isinstance(x, dict)]
        except Exception as exc:
            logger.warning("ReviewBank %s: битый файл (%s) — пустой банк", self.student_id, exc)
            return []

    def _save(self, cards: List[ReviewCard]) -> None:
        p = self._path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([c.to_dict() for c in cards], ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)

    def get(self, card_id: str) -> Optional[ReviewCard]:
        return next((c for c in self._load() if c.card_id == card_id), None)

    def add_from_record(self, record: Dict[str, Any]) -> bool:
        """Добавить/освежить карточку по записи ошибочного вопроса. True — добавлена."""
        question = str(record.get("question") or "").strip()
        if not question:
            return False
        cid = card_id_for(question)
        cards = self._load()
        existing = next((c for c in cards if c.card_id == cid), None)
        now = _now_iso()
        if existing is None:
            cards.append(ReviewCard(
                card_id=cid, student_id=self.student_id,
                subject=str(record.get("subject") or ""), topic=str(record.get("topic") or ""),
                question=question, options=record.get("options"),
                answer_type=str(record.get("answer_type") or "open"),
                correct_answer=str(record.get("correct_answer") or ""),
                difficulty=str(record.get("difficulty") or "medium"),
                added_at=now, last_reviewed="", due_at=now, interval_days=1.0, ease=2.5,
            ))
        else:
            existing.topic = str(record.get("topic") or existing.topic)
            existing.subject = str(record.get("subject") or existing.subject)
            existing.correct_answer = str(record.get("correct_answer") or existing.correct_answer)
            existing.last_reviewed = ""
        cards = cards[-self.max_cards:]
        self._save(cards)
        return existing is None

    def get_due(self, subject: Optional[str] = None, limit: int = 5) -> List[ReviewCard]:
        cards = self._load()
        due = [c for c in cards if c.is_due and (not subject or c.subject.lower() == subject.lower())]
        due.sort(key=lambda c: _parse_iso(c.due_at) or datetime.datetime.min)
        return due[: int(limit or 5)]

    def review_card(self, card_id: str, correct: bool) -> Optional[ReviewCard]:
        cards = self._load()
        c = next((x for x in cards if x.card_id == card_id), None)
        if c is None:
            return None
        now = datetime.datetime.now()
        if correct:
            c.reps += 1
            if c.reps == 1:
                c.interval_days = 1.0
            else:
                c.interval_days = round(c.interval_days * c.ease, 1)
            c.ease = max(1.3, round(c.ease + (0.1 - max(0, 3 - c.reps) * 0.05), 2))
        else:
            c.reps = 0
            c.interval_days = 1.0
            c.lapses += 1
            c.ease = max(1.3, round(c.ease - 0.2, 2))
        c.last_reviewed = now.isoformat(timespec="seconds")
        c.due_at = (now + datetime.timedelta(days=c.interval_days)).isoformat(timespec="seconds")
        self._save(cards)
        return c

    def stats(self) -> Dict[str, Any]:
        cards = self._load()
        due = [c for c in cards if c.is_due]
        by_topic: Dict[str, int] = {}
        for c in cards:
            by_topic[c.topic] = by_topic.get(c.topic, 0) + 1
        return {
            "total": len(cards),
            "due": len(due),
            "lapses": sum(c.lapses for c in cards),
            "by_topic": by_topic,
        }

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self._load()]
