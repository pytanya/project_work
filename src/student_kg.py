"""
Student Knowledge Graph (roadmap #4): персистентный JSON-профиль ученика.

Проблема: Wiki хранит мастерство по каждой теме, KnowledgeGraph — структуру
учебника. Нет единого профиля «какие темы изучал ученик и в каком статусе»,
который переживал бы сессии и служил основой для адаптивного обучения.

Решение: StudentKnowledgeGraph — dict {topic_id: TopicStatus}, хранится в
StudentProfile (data/students/<student_id>.json). Поле `topics` содержит:
- status: "not_studied" | "in_progress" | "mastered"
- mastery: 0.0-1.0 (из wiki/quiz)
- attempts, correct: из wiki
- weak_areas: список слабых мест (из wiki notes)
- last_studied: ISO-дата последнего изучения
- relations: {prerequisite: [...], related: [...]} — из KnowledgeGraph учебника

Источники обновления:
- WikiArticle.apply_result → mastery/attempts/weak_areas
- Quiz summary → sync knowledge_map → mastery
- Lesson completion → mark topic as "in_progress"
- Agent adaptive selection → mark prerequisite gaps

API:
- GET /students/<id>/knowledge-graph → полный граф
- POST /students/<id>/knowledge-graph/update → batch-update (from quiz/wiki)

Фронтенд:
- KnowledgeGraphPanel показывает узлы с цветовой кодировкой статусов
- Agent использует KG для выбора тем (пропуск освоенных, фокус на слабом)
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .config import settings as default_settings

logger = logging.getLogger("edututor.student_kg")

STATUS_LITERAL = Literal["not_studied", "in_progress", "mastered"]


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class TopicStatus(BaseModel):
    """Статус одной темы в knowledge graph ученика."""

    topic_id: str
    title: str = ""
    subject: str = ""
    status: STATUS_LITERAL = "not_studied"
    mastery: float = 0.0
    attempts: int = 0
    correct: int = 0
    weak_areas: List[str] = Field(default_factory=list)
    last_studied: str = ""
    relations: Dict[str, List[str]] = Field(default_factory=lambda: {"prerequisite": [], "related": []})
    curriculum_code: Optional[str] = None

    @property
    def accuracy(self) -> float:
        return round(self.correct / self.attempts, 4) if self.attempts else 0.0

    @property
    def is_mastered(self) -> bool:
        return self.status == "mastered" or (self.mastery >= 0.8 and self.attempts >= 3)

    @property
    def is_in_progress(self) -> bool:
        return self.status == "in_progress" or (0 < self.attempts < 3)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, topic_id: str, data: Dict[str, Any]) -> "TopicStatus":
        return cls(
            topic_id=topic_id,
            title=data.get("title", topic_id),
            subject=data.get("subject", ""),
            status=data.get("status", "not_studied"),
            mastery=data.get("mastery", 0.0),
            attempts=data.get("attempts", 0),
            correct=data.get("correct", 0),
            weak_areas=data.get("weak_areas", []),
            last_studied=data.get("last_studied", ""),
            relations=data.get("relations", {}),
            curriculum_code=data.get("curriculum_code"),
        )


class StudentKnowledgeGraph(BaseModel):
    """Персистентный граф знаний ученика: {topic_id: TopicStatus}."""

    student_id: str
    subject: str = ""
    topics: Dict[str, TopicStatus] = Field(default_factory=dict)
    updated_at: str = ""

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def get_topic(self, topic_id: str) -> Optional[TopicStatus]:
        return self.topics.get(topic_id)

    def set_topic(
        self,
        topic_id: str,
        title: str = "",
        subject: str = "",
        mastery: float = 0.0,
        attempts: int = 0,
        correct: int = 0,
        weak_areas: Optional[List[str]] = None,
        relations: Optional[Dict[str, List[str]]] = None,
        curriculum_code: Optional[str] = None,
    ) -> TopicStatus:
        """Upsert topic status. Если attempts > 0 и mastery >= 0.8 → mastered."""
        now = _now_iso()
        prev = self.topics.get(topic_id)

        if prev:
            # Аккумулируем данные: attempts/weak_areas/relations не перезаписываем
            attempts = max(prev.attempts, attempts)
            correct = max(prev.correct, correct)
            weak_areas = list(set(prev.weak_areas + (weak_areas or [])))
            relations = _merge_relations(prev.relations, relations or {})
            curriculum_code = curriculum_code or prev.curriculum_code
            title = title or prev.title
            subject = subject or prev.subject

        # Авто-переход в mastered: mastery >= 0.8 и >= 3 попыток
        if attempts >= 3 and mastery >= 0.8:
            status: STATUS_LITERAL = "mastered"
        elif attempts > 0:
            status = "in_progress"
        else:
            status = "not_studied"

        ts = TopicStatus(
            topic_id=topic_id,
            title=title or topic_id,
            subject=subject or prev.subject if prev else subject,
            status=status,
            mastery=round(mastery, 4),
            attempts=attempts,
            correct=correct,
            weak_areas=weak_areas or [],
            last_studied=now,
            relations=relations or {},
            curriculum_code=curriculum_code,
        )
        self.topics[topic_id] = ts
        self.touch()
        return ts

    def mark_in_progress(self, topic_id: str, title: str = "", subject: str = "") -> TopicStatus:
        """Тема начата (урок показан, квиз не пройден) → in_progress."""
        return self.set_topic(
            topic_id=topic_id,
            title=title,
            subject=subject,
            status="in_progress",
            last_studied=_now_iso(),
        )

    def mark_mastered(self, topic_id: str, mastery: float = 1.0) -> TopicStatus:
        """Тема освоена (квиз пройден с высоким баллом) → mastered."""
        return self.set_topic(
            topic_id=topic_id,
            mastery=mastery,
            status="mastered",
            last_studied=_now_iso(),
        )

    def sync_from_wiki(self, wiki_articles: List[Dict[str, Any]]) -> int:
        """Синхронизация из wiki-статей (mastery/attempts/weak_areas).

        Возвращает количество обновлённых тем.
        """
        updated = 0
        for art in wiki_articles:
            topic_id = art.get("topic", "")
            if not topic_id:
                continue
            subject = art.get("subject", "")
            mastery = art.get("mastery", 0.0)
            attempts = art.get("attempts", 0)
            correct = art.get("correct", 0)
            weak_areas = art.get("weak_areas", [])

            self.set_topic(
                topic_id=topic_id,
                title=art.get("title", topic_id),
                subject=subject,
                mastery=mastery,
                attempts=attempts,
                correct=correct,
                weak_areas=weak_areas,
                last_studied=art.get("last_studied", _now_iso()),
            )
            updated += 1
        return updated

    def sync_from_knowledge_map(self, knowledge_map: Dict[str, float]) -> int:
        """Синхронизация из knowledge_map (квиз завершён, мастерство по темам).

        knowledge_map: {topic: mastery_score}. Возвращает количество обновлённых.
        """
        updated = 0
        for topic_id, mastery in knowledge_map.items():
            if not topic_id:
                continue
            self.set_topic(
                topic_id=topic_id,
                mastery=mastery,
                last_studied=_now_iso(),
            )
            updated += 1
        return updated

    def get_mastered_topics(self, subject: Optional[str] = None) -> List[TopicStatus]:
        """Освоенные темы (по предмету, если указан)."""
        return sorted(
            [ts for ts in self.topics.values() if ts.is_mastered and (not subject or ts.subject == subject)],
            key=lambda x: -x.mastery,
        )

    def get_in_progress_topics(self, subject: Optional[str] = None) -> List[TopicStatus]:
        """Темы в процессе изучения."""
        return sorted(
            [ts for ts in self.topics.values() if ts.is_in_progress and (not subject or ts.subject == subject)],
            key=lambda x: -x.last_studied,
        )

    def get_weak_topics(self, subject: Optional[str] = None, threshold: float = 0.5) -> List[TopicStatus]:
        """Слабые темы (accuracy < threshold, attempts >= 2)."""
        return sorted(
            [
                ts for ts in self.topics.values()
                if ts.attempts >= 2 and ts.accuracy < threshold
                and (not subject or ts.subject == subject)
            ],
            key=lambda x: x.accuracy,
        )

    def get_prerequisite_gaps(self, topic_id: str) -> List[str]:
        """Пререквизиты темы, которые не изучены или не освоены."""
        ts = self.topics.get(topic_id)
        if not ts:
            return []
        gaps = []
        for prereq_id in ts.relations.get("prerequisite", []):
            prereq = self.topics.get(prereq_id)
            if not prereq or not prereq.is_mastered:
                gaps.append(prereq_id)
        return gaps

    def get_recommended_topics(
        self,
        current_topic: Optional[str] = None,
        subject: Optional[str] = None,
        limit: int = 5,
    ) -> List[TopicStatus]:
        """Рекомендации: слабые темы + неосвоенные пререквизиты.

        Приоритет: слабые места > in_progress > not_studied (с пререквизитами).
        """
        # 1. Слабые темы (accuracy < 0.5, attempts >= 2)
        weak = self.get_weak_topics(subject=subject)

        # 2. Неосвоенные пререквизиты текущей темы
        if current_topic:
            gaps = self.get_prerequisite_gaps(current_topic)
            for gap_id in gaps:
                gap = self.topics.get(gap_id)
                if gap and gap not in weak:
                    weak.append(gap)

        # 3. Темы в процессе (не законченные)
        in_progress = self.get_in_progress_topics(subject=subject)

        # 4. Not studied (без пререквизитов)
        not_studied = [
            ts for ts in self.topics.values()
            if ts.status == "not_studied" and (not subject or ts.subject == subject)
            and not self.get_prerequisite_gaps(ts.topic_id)
        ]

        # Собираем: weak + in_progress + not_studied, без дублей
        seen = set()
        result: List[TopicStatus] = []
        for ts in weak + in_progress + not_studied:
            if ts.topic_id not in seen:
                seen.add(ts.topic_id)
                result.append(ts)
            if len(result) >= limit:
                break
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "student_id": self.student_id,
            "subject": self.subject,
            "topics": {k: v.to_dict() for k, v in self.topics.items()},
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StudentKnowledgeGraph":
        topics = {
            k: TopicStatus.from_dict(k, v) for k, v in (data.get("topics") or {}).items()
        }
        return cls(
            student_id=data.get("student_id", ""),
            subject=data.get("subject", ""),
            topics=topics,
            updated_at=data.get("updated_at", ""),
        )


def _merge_relations(
    prev: Dict[str, List[str]],
    new: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    """Объединение списков relations (prerequisite/related) без дублей."""
    merged: Dict[str, List[str]] = {}
    all_keys = set(prev.keys()) | set(new.keys())
    for key in all_keys:
        merged[key] = list(set((prev.get(key) or []) + (new.get(key) or [])))
    return merged


class StudentKnowledgeGraphStore:
    """Хранилище knowledge graph учеников (JSON по файлу в StudentProfile).

    Граф хранится как поле `knowledge_graph` в StudentProfile.
    """

    def __init__(self, student_store: Any = None) -> None:
        self.student_store = student_store

    def get(self, student_id: str) -> Optional[StudentKnowledgeGraph]:
        if not student_id:
            return None
        if self.student_store is None:
            return None
        profile = self.student_store.get(student_id)
        if profile is None:
            return None
        kg_data = getattr(profile, "knowledge_graph", None) or {}
        if not kg_data:
            return None
        try:
            return StudentKnowledgeGraph.from_dict(kg_data)
        except Exception:
            logger.warning("Не удалось прочитать knowledge graph %s", student_id)
            return None

    def save(self, student_id: str, kg: StudentKnowledgeGraph) -> None:
        if self.student_store is None:
            return
        profile = self.student_store.get(student_id)
        if profile is None:
            return
        profile.knowledge_graph = kg.to_dict()  # type: ignore[attr-defined]
        self.student_store.save(profile)

    def update_batch(
        self,
        student_id: str,
        subject: str,
        wiki_articles: Optional[List[Dict[str, Any]]] = None,
        knowledge_map: Optional[Dict[str, float]] = None,
        in_progress_topics: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Batch-update knowledge graph из wiki + knowledge_map + in_progress.

        Возвращает количество обновлённых тем.
        """
        kg = self.get(student_id)
        if kg is None:
            kg = StudentKnowledgeGraph(student_id=student_id, subject=subject)
        else:
            kg.subject = subject

        updated = 0

        if wiki_articles:
            updated += kg.sync_from_wiki(wiki_articles)

        if knowledge_map:
            updated += kg.sync_from_knowledge_map(knowledge_map)

        if in_progress_topics:
            for topic in in_progress_topics:
                topic_id = topic.get("topic_id", topic.get("topic", ""))
                if topic_id:
                    kg.mark_in_progress(
                        topic_id=topic_id,
                        title=topic.get("title", topic_id),
                        subject=topic.get("subject", subject),
                    )
                    updated += 1

        self.save(student_id, kg)
        return updated
