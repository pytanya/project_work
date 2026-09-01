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
from typing import Any, Dict, List, Literal, Optional, Tuple

from .states import IntakeState

INTAKE_QUESTIONS: Dict[str, str] = {
    "learner_type": "Для кого готовим материал — ученик какого класса или студент?",
    "grade": "Какой у тебя класс? (например: 6)",
    "subject": "Какой предмет изучаем? (например: математика, физика)",
    "topic": "Какая тема или раздел? (например: Дроби)",
    "has_textbook": "Есть ли у тебя учебник по этой теме? (да/нет)",
    "mode": "Что делаем: изучим тему (урок), проверим знания (квиз), объясню конкретный вопрос или сделаем глубокий разбор?",
}

# Порядок вопросов чек-листа (5.1). chapter исключён: он дублирует topic и
# определяется автоматически после индексации по графу знаний.
CHECKLIST_ORDER = [
    "learner_type", "grade", "subject", "topic", "has_textbook", "mode",
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
    # Типизированные значения (например, из extract_intake_fields) нормализуются идемпотентно
    if isinstance(value, bool):
        text = "да" if value else "нет"
    else:
        text = str(value).strip().lower()
    if not text:
        return None
    if text in _UNKNOWN:
        return None

    if field == "learner_type":
        # типизированное значение (extract_intake_fields) — идемпотентно
        if isinstance(value, str) and value in ("student", "schoolchild"):
            return value
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


def extract_intake_fields(text: str) -> Dict[str, Any]:
    """Много-полевое извлечение из свободного ответа (5.4 / инструмент set_intake).

    Один развёрнутый ответ ученика может заполнить несколько полей чек-листа:
    «я в 7 классе, алгебра, дроби, учебника нет, хочу квиз».
    Возвращает только поля, распознанные уверенно. topic — только при явном маркере
    («тема/тему/раздел/глава»), иначе тема остаётся на следующий уточняющий вопрос.
    """
    from .nlp import SUBJECTS  # локальный импорт — без циклической зависимости

    result: Dict[str, Any] = {}
    t = (text or "").strip()
    if not t or t.lower() in _UNKNOWN:
        return result
    low = t.lower()

    # Учебник
    segments = [s.strip().lower() for s in low.split(",")]
    if low in _YES or any(s in _YES for s in segments):
        result["has_textbook"] = True
    elif low in _NO or any(s in _NO for s in segments):
        result["has_textbook"] = False
    else:
        if any(ph in low for ph in ("есть учебник", "учебник есть", "имеется учебник")):
            result["has_textbook"] = True
        elif any(ph in low for ph in ("учебника нет", "нет учебника", "учебник отсутствует")):
            result["has_textbook"] = False

    # Режим
    for key, mode in _MODE_MAP.items():
        if len(key) >= 3 and key in low:
            result["mode"] = mode
            break

    # Тип обучаемого + класс
    if "студент" in low or "student" in low:
        result["learner_type"] = "student"
    elif "ученик" in low or "школьник" in low or re.search(r"\d{1,2}\s*класс", low):
        result["learner_type"] = "schoolchild"
    g = re.search(r"(\d{1,2})\s*(?:-?й)?\s*класс", low)
    if g:
        result["grade"] = g.group(1)
    elif re.fullmatch(r"\d{1,2}", t.strip()):
        # «7» в ответ на «какой класс?» — класс
        result["grade"] = t.strip()

    # Предмет
    for subj in SUBJECTS:
        if subj in low:
            result["subject"] = subj
            break

    # Тема — «весь учебник» (сегмент «все»/«всё») или с явным маркером («тема X»)
    all_markers = ("все", "всё", "вся", "весь", "весь учебник", "все темы")
    if any(seg.strip() in all_markers for seg in low.split(",")):
        result["topic"] = "all"
    else:
        m = re.search(
            r"(?:тема|тему|раздел|глава|главу)\s+[\"«]?([а-яёА-ЯЁa-z0-9 _\-]{2,60})[\"»]?",
            t, re.IGNORECASE,
        )
        if m:
            candidate = m.group(1).strip().strip(".,;")
            if (
                candidate
                and candidate not in _YES
                and candidate not in _NO
                and not any(kw in candidate for kw in _MODE_MAP)
            ):
                result["topic"] = candidate
    return result


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


def maybe_start_card(state: IntakeState) -> Tuple[IntakeState, bool]:
    """Если intake только начался и ещё не завершён — подготовить карточку знакомства.

    Возвращает (state, started). Карточка не пересоздаётся, если уже есть или если
    intake уже продвинулся (intake_iterations > 0 — ученик начал отвечать текстом).
    """
    if compute_missing(state) and state.agent_card is None and state.intake_iterations <= 0:
        st = state.model_copy(deep=True)
        st.agent_card = build_intake_card(st)
        st.agent_question = st.agent_card["question"]
        st.intake_field = None
        return st, True
    return state, False


def emergency_min_set_met(state: IntakeState) -> bool:
    """Минимальный набор для экстренного старта (5.2): тема/предмет + режим + тип."""
    return bool((state.topic or state.subject) and state.mode and state.learner_type)


# ----------------------------------------------------------------------
# Карточка intake (быстрое заполнение вместо пошагового Q&A)
# ----------------------------------------------------------------------
_MODE_CARD_OPTIONS = [
    {"value": "lesson", "label": "Урок (изучим тему)"},
    {"value": "quiz", "label": "Квиз (проверим знания)"},
]
# Расширенные режимы: доступны через чат-сообщения после заполнения карточки
_MODE_ADVANCED_OPTIONS = [
    {"value": "explain", "label": "Объяснение конкретного вопроса"},
    {"value": "deep_dive", "label": "Глубокий разбор"},
]


def build_intake_card(state: IntakeState) -> Dict[str, Any]:
    """Структурированная карточка знакомства/плана занятия.

    Поля предзаполняются из состояния/профиля (для вернувшегося ученика —
    имя/тип/класс уже известны, осталось указать предмет/тему/режим).
    Возвращает JSON-форму: {title, question, fields: [{key,label,type,options,value}]}.
    """
    def _choice(options: List[Dict[str, str]], value: Optional[str]) -> List[Dict[str, str]]:
        return options

    textbook = None
    if state.has_textbook is not None:
        textbook = "true" if state.has_textbook else "false"

    fields: List[Dict[str, Any]] = [
        {"key": "name", "label": "Как тебя зовут (имя и фамилия)?", "type": "text", "required": True,
         "value": getattr(state, "student_name", None) or ""},
        {"key": "learner_type", "label": "Ты школьник или студент?", "type": "choice", "required": True,
         "options": _choice([
             {"value": "schoolchild", "label": "Школьник"},
             {"value": "student", "label": "Студент"},
         ], state.learner_type),
         "value": state.learner_type or ""},
        {"key": "grade", "label": "Класс (если школьник)", "type": "text", "required": False,
         "value": state.grade or ""},
        {"key": "subject", "label": "Предмет", "type": "text", "required": True,
         "value": state.subject or ""},
        {"key": "topic", "label": "Тема", "type": "text", "required": True,
         "value": state.topic or ""},
        {"key": "has_textbook", "label": "Есть учебник по теме?", "type": "choice", "required": True,
         "options": _choice([
             {"value": "true", "label": "Да"},
             {"value": "false", "label": "Нет"},
         ], textbook),
         "value": textbook or ""},
        {"key": "mode", "label": "Что делаем?", "type": "choice", "required": True,
         "options": _choice(_MODE_CARD_OPTIONS, state.mode),
         "value": state.mode or ""},
    ]
    return {
        "title": "Знакомство и план занятия",
        "question": "Заполни карточку — так быстрее, чем отвечать на вопросы по одному.",
        "fields": fields,
    }


def apply_intake_card(
    state: IntakeState,
    values: Dict[str, Any],
    student_id: Optional[str] = None,
) -> IntakeState:
    """Применить заполненную карточку к состоянию (все поля сразу).

    Возвращает копию состояния; `agent_card` сбрасывается. Значения нормализуются
    детерминированно (те же правила, что и в normalize_answer/extract_intake_fields).

    Если передан `student_id` и он отличается от текущего — сессия перепривязывается
    к нему (namespace Wiki/истории/мастерства). Это сигнал с фронта, что карточка
    заполнена ДРУГИМ человеком (другое ФИО/тип/класс): выделяем новую изолированную
    ветку данных, не трогая данные предыдущего ученика.
    """
    st = state.model_copy(deep=True)
    v = values or {}
    st.agent_card = None

    if student_id and student_id != st.student_id:
        st.student_id = student_id

    name = str(v.get("name") or "").strip()
    if name:
        st.student_name = name

    if v.get("learner_type") in ("student", "schoolchild"):
        st.learner_type = v["learner_type"]

    g = re.search(r"(\d{1,2})", str(v.get("grade") or ""))
    if g:
        st.grade = g.group(1)

    for field in ("subject", "topic"):
        val = str(v.get(field) or "").strip()
        if val:
            setattr(st, field, val)

    hb = v.get("has_textbook")
    if isinstance(hb, bool):
        st.has_textbook = hb
    elif isinstance(hb, str):
        st.has_textbook = hb.strip().lower() in ("true", "да", "yes", "1")

    mode = str(v.get("mode") or "").strip().lower()
    if mode in ("quiz", "lesson", "explain", "deep_dive"):
        st.mode = mode  # type: ignore[assignment]
    elif mode in _MODE_MAP:
        st.mode = _MODE_MAP[mode]  # type: ignore[assignment]

    st.intake_iterations += 1
    st.intake_field = None
    st.intake_progress = len(set(CHECKLIST_ORDER) - set(compute_missing(st)))
    st.missing_fields = compute_missing(st)
    return st
