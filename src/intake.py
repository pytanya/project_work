"""
EduTutor — intake-фаза: чек-лист + validate_intake (раздел 5).

- INTAKE_QUESTIONS: порядок вопросов чек-листа.
- compute_missing: обязательные/опциональные поля (5.2).
- normalize_answer: приведение ответа к значению поля (морфология).
- apply_answer: установка поля + счётчик итераций + контроль прогресса (В-3).
- validate_intake: решение — ask / start / emergency_start (В-3, Ж-5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from .states import IntakeState

INTAKE_QUESTIONS: Dict[str, str] = {
    "learner_type": "Кто ты? (студент / ученик N класса)",
    "grade": "В каком ты классе? (5-11)",
    "subject": "Какой предмет изучаем?",
    "topic": "Какая тема или раздел?",
    "has_textbook": "Есть ли у тебя учебник? (да/нет)",
    "chapter": "Какая глава/раздел нужна? (или 'все')",
    "mode": "Что будем делать: урок (изучить тему), квиз, объяснение или глубокий разбор?",
}

# Порядок вопросов чек-листа (5.1)
CHECKLIST_ORDER = [
    "learner_type", "grade", "subject", "topic", "has_textbook", "chapter", "mode",
]

_MODE_MAP = {
    "квиз": "quiz", "квизы": "quiz", "тест": "quiz", "quiz": "quiz",
    "объяснение": "explain", "объясни": "explain", "explain": "explain",
    "урок": "lesson", "изучить": "lesson", "изучение": "lesson",
    "изучить тему": "lesson", "lesson": "lesson", "обучение": "lesson",
    "глубокий разбор": "deep_dive", "deep dive": "deep_dive",
    "deep_dive": "deep_dive", "глубоко": "deep_dive", "подробно": "deep_dive",
}
_YES = {"да", "yes", "у", "есть", "ага", "конечно"}
_NO = {"нет", "no", "не", "н", "неа"}
_UNKNOWN = {"не знаю", "не знаю.", "нз", "не понимаю", "хз", "без понятия"}


@dataclass
class IntakeDecision:
    """Решение валидатора достаточности данных (5.2)."""

    decision: Literal["ask", "start", "emergency_start"]
    missing_fields: List[str] = field(default_factory=list)
    next_question: str = ""
    warning: str = ""


def compute_missing(state: IntakeState) -> List[str]:
    """Обязательные поля для ПОЛНОЦЕННОГО старта (5.2, таблица)."""
    missing: List[str] = []
    if state.learner_type is None:
        missing.append("learner_type")
    elif state.learner_type == "schoolchild" and not state.grade:
        missing.append("grade")
    if not state.subject and not state.topic:
        missing.append("subject")
    # Если предмет/дисциплина указаны, но тема не конкретизирована — запрашиваем тему
    if state.subject and not state.topic:
        missing.append("topic")
    if state.has_textbook is None:
        missing.append("has_textbook")
    if state.mode is None:
        missing.append("mode")
    return missing


def next_question(state: IntakeState) -> str:
    """Текст следующего вопроса чек-листа (первое недостающее обязательное поле)."""
    missing = compute_missing(state)
    for field_name in CHECKLIST_ORDER:
        if field_name in missing:
            return INTAKE_QUESTIONS[field_name]
    return ""


def normalize_answer(field: str, value: str) -> Any:
    """Приведение ответа пользователя к значению поля. None — «не знаю»/неопределённо."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _UNKNOWN:
        return None

    if field == "learner_type":
        if "студент" in text or "student" in text:
            return "student"
        if "ученик" in text or "школьник" in text or "класс" in text:
            return "schoolchild"
        return None

    if field == "grade":
        m = re.search(r"(\d{1,2})", text)
        return m.group(1) if m else None

    if field == "subject" or field == "topic" or field == "chapter":
        if field in ("chapter", "topic") and text in ("все", "вся", "all", "весь", "всё", "весь учебник"):
            return "all"
        if text in _YES or text in _NO:
            return None  # ответ не по теме вопроса
        if field == "topic" and text in _MODE_MAP:
            return None  # ответ-режим (квиз/урок/объяснение/...) — не тема, переспрашиваем
        return str(value).strip()

    if field == "has_textbook":
        if text in _YES:
            return True
        if text in _NO:
            return False
        return None

    if field == "mode":
        for key, mode in _MODE_MAP.items():
            if key in text:
                return mode
        return None

    return str(value).strip()


def apply_answer(state: IntakeState, field: str, value: str) -> IntakeState:
    """Установка ответа + контроль прогресса (В-3): intake_progress, streak."""
    parsed = normalize_answer(field, value)
    missing_before = set(compute_missing(state))

    if parsed is not None:
        setattr(state, field, parsed)
    elif field in ("learner_type", "has_textbook", "grade", "mode") and parsed is None:
        # «не знаю» — поле остаётся пустым, прогресса нет
        pass

    missing_after = set(compute_missing(state))
    state.intake_iterations += 1
    newly_closed = len(missing_before - missing_after)
    state.intake_progress = newly_closed
    if newly_closed <= 0:
        state.intake_no_progress_streak += 1
    else:
        state.intake_no_progress_streak = 0

    state.missing_fields = list(missing_after)
    return state


def validate_intake(
    state: IntakeState,
    max_iterations: Optional[int] = None,
) -> IntakeDecision:
    """Валидация достаточности (5.2). Логика п. 1–3:
    1) missing непусто и итераций меньше лимита → ask;
    2) 2 итерации без прогресса → экстренный старт;
    3) итераций ≥ лимита → экстренный старт.
    """
    if max_iterations is None:
        from .config import settings

        max_iterations = settings.MAX_INTAKE_ITERATIONS

    missing = compute_missing(state)
    if not missing:
        return IntakeDecision(decision="start", missing_fields=[], next_question="")

    if state.intake_no_progress_streak >= 2:
        return IntakeDecision(
            decision="emergency_start",
            missing_fields=missing,
            next_question="",
            warning=(
                "2 итерации без прогресса — стартуем с минимальным набором "
                "(тема/предмет + режим + тип). Работаем без класса/учебника, точность ниже."
            ),
        )

    if state.intake_iterations >= max_iterations:
        return IntakeDecision(
            decision="emergency_start",
            missing_fields=missing,
            next_question="",
            warning="Исчерпан лимит уточняющих итераций — экстренный старт с минимальным набором.",
        )

    return IntakeDecision(
        decision="ask",
        missing_fields=missing,
        next_question=next_question(state),
    )


def emergency_min_set_met(state: IntakeState) -> bool:
    """Минимальный набор для экстренного старта (5.2): тема/предмет + режим + тип."""
    return bool((state.topic or state.subject) and state.mode and state.learner_type)
