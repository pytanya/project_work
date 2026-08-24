"""
EduTutor — инструменты агентного цикла `agent_loop` (спека 2.3, 7.3).

Детерминированный функционал тьютора вынесен в инструменты (function calling):
модель решает, какой инструмент вызвать и с какими аргументами; инструмент
возвращает JSON-результат и (при необходимости) обновляет состояние.

Каждый инструмент: fn(arguments: dict, ctx: AgentToolContext) -> (result_str, state).
`ctx` инжектируется вызывающим слоем (agent_loop), не сериализуется в результат.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .intake import CHECKLIST_ORDER, INTAKE_QUESTIONS, apply_answer, compute_missing, extract_intake_fields
from .intake import build_intake_card as _build_intake_card
from .states import TutorState
from . import tutor as tutor_mod

MAX_TOOL_RESULT_CHARS = 4000


def _active_topic_title(st: TutorState) -> str:
    """Заголовок активной темы из графа знаний (если активная тема задана)."""
    if not st.active_topic or not st.knowledge_graph:
        return ""
    for n in st.knowledge_graph.get("nodes", []):
        if n.get("id") == st.active_topic:
            return n.get("title", "")
    return ""


@dataclass
class AgentToolContext:
    """Контекст инструмента: состояние сессии + зависимости графа + агентная LLM."""

    state: TutorState
    deps: Any = None  # GraphDeps: store, embedder, settings, tutor_llm/expert_llm
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None
    on_token: Optional[Callable[[str], None]] = None


def _ok(**data: Any) -> str:
    body = json.dumps({"ok": True, **data}, ensure_ascii=False)
    return body[:MAX_TOOL_RESULT_CHARS]


def _err(message: str, **data: Any) -> str:
    return json.dumps({"ok": False, "error": message, **data}, ensure_ascii=False)


# ----------------------------------------------------------------------
# Инструменты интервью (5.4)
# ----------------------------------------------------------------------
def interview_progress(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Какие поля чек-листа заполнены/пусты; какой следующий вопрос."""
    st = ctx.state
    missing = compute_missing(st)
    filled = [f for f in CHECKLIST_ORDER if f not in missing]
    next_q = INTAKE_QUESTIONS[missing[0]] if missing else ""
    return _ok(missing_fields=missing, filled_fields=filled, next_question=next_q), st


def build_intake_card(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Карточка знакомства: структурированная форма (имя, тип, класс, предмет, тема, режим).

    Быстрее, чем пошаговое интервью: ученик заполняет поля сразу. Модель вызывает
    инструмент, когда решает, что интервью затянется; карточка кладётся в
    state.agent_card и показывается ученику формой.
    """
    st = ctx.state
    card = _build_intake_card(st)
    st = st.model_copy(deep=True)
    st.agent_card = card
    st.agent_question = card["question"]
    st.intake_field = None
    return _ok(card=card, fields=[f["key"] for f in card["fields"]]), st


def set_intake(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Записать одно поле чек-листа (значение нормализуется детерминированно)."""
    field = str(args.get("field") or "")
    value = str(args.get("value") or "")
    st = ctx.state.model_copy(deep=True)
    if field not in CHECKLIST_ORDER:
        return _err(f"Неизвестное поле чек-листа: {field!r}", field=field), st
    if field not in compute_missing(st):
        # Поле уже заполнено (например, детерминированным слоем) — не трогаем,
        # иначе инкрементируем streak «без прогресса» и провоцируем emergency_start.
        return _ok(field=field, accepted=False, already=True,
                   still_missing=compute_missing(st)), st
    missing_before = compute_missing(st)
    st = apply_answer(st, field, value)
    missing_after = compute_missing(st)
    closed = len(set(missing_before) - set(missing_after))
    progress = 1 if closed > 0 else 0
    return _ok(field=field, accepted=closed > 0, closed=closed,
               still_missing=missing_after, progress=progress), st


# ----------------------------------------------------------------------
# Инструменты retrieval
# ----------------------------------------------------------------------
def _rag_results(ctx: AgentToolContext, query: str, k: int = 5) -> List[Any]:
    store = getattr(ctx.deps, "store", None)
    if store is None:
        return []
    filters: Dict[str, Any] = {}
    st = ctx.state
    if st.subject:
        filters["subject"] = st.subject
    if st.grade:
        filters["grade"] = st.grade
    if getattr(st, "student_id", None):
        filters["student_id"] = st.student_id
    try:
        return store.search(query, k=k, filters=filters or None)
    except Exception:
        return []


def rag_search(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Семантический поиск по коллекции учебника (фильтр по предмету/классу/разделу)."""
    query = str(args.get("query") or "")
    k = int(args.get("k") or 5)
    if not query:
        return _err("query пуст"), ctx.state
    results = _rag_results(ctx, query, k=k)
    items = [
        {
            "text": r.chunk.text[:500],
            "section": r.chunk.section_number,
            "source": r.chunk.source,
            "score": round(r.score, 4),
        }
        for r in results
    ]
    if not items:
        return _ok(results=[], message="Релевантного контекста не найдено — можно вызвать route_to_source для дополнения источников"), ctx.state
    return _ok(results=items, count=len(items)), ctx.state


def get_knowledge_graph(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Граф знаний учебника: темы/уроки + связи (part_of/prerequisite/related)."""
    kg = ctx.state.knowledge_graph or {}
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])
    return _ok(
        nodes=[{"id": n.get("id"), "title": n.get("title"), "type": n.get("type")} for n in nodes],
        edges=edges[:200],
        active_topic=ctx.state.active_topic,
    ), ctx.state


# ----------------------------------------------------------------------
# Инструменты тьюторинга (7.3)
# ----------------------------------------------------------------------
def generate_lesson(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Урок: синтез структурированного объяснения темы по RAG-контексту."""
    st = ctx.state
    topic = str(args.get("topic") or _active_topic_title(st) or st.topic or st.subject or "общая тема")
    results = _rag_results(ctx, topic, k=5)
    # RAG-first гейт: без контекста урок не выдумываем — агенту нужно собрать материалы
    if not results:
        return _err(
            "Контекста по теме нет. Сначала соберите материалы (route_to_source), "
            "затем повторите generate_lesson.",
            required_action="route_to_source"
        ), st
    context = [r.chunk.text for r in results]
    # Урок — структурированный JSON: без on_token (не стримим сырой JSON)
    lesson = tutor_mod.generate_lesson(topic, context, st, llm_call=ctx.llm_call)
    st = st.model_copy(deep=True)
    st.set_lesson(lesson)
    st.lesson_done = True
    return _ok(topic=topic, text=st.lesson_text, lesson=st.lesson_payload(topic)), st


def generate_quiz(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Следующий вопрос квиза (с антидубликатом 7.3.2 и записью в records)."""
    # Страховка: если mode="lesson" и урок не показан — запретить квиз
    st = ctx.state
    if (st.mode == "lesson" and not st.lesson_done):
        return _err(
            "Сначала сгенерируйте урок (generate_lesson), чтобы ученик изучил материал. Только после этого — generate_quiz.",
            required_action="generate_lesson"
        ), st
    from datetime import datetime

    st = st.model_copy(deep=True)
    topic = str(args.get("topic") or _active_topic_title(st) or st.topic or st.subject or "общая тема")
    difficulty = str(args.get("difficulty") or st.difficulty or "medium")
    context = [r.chunk.text for r in _rag_results(ctx, topic, k=3)] or ["Нет контекста по теме."]
    prev_asked = list(st.asked_questions)
    retries = getattr(getattr(ctx.deps, "settings", None), "QUESTION_DEDUPE_RETRIES", 2)
    threshold = getattr(getattr(ctx.deps, "settings", None), "QUESTION_DEDUPE_THRESHOLD", 0.85)
    card = None
    for _ in range(retries + 1):
        card = tutor_mod.generate_question(topic, context, difficulty, st, llm_call=ctx.llm_call, on_token=ctx.on_token)
        if not prev_asked or not tutor_mod.is_duplicate_question(
            getattr(ctx.deps, "embedder", None), card.question, prev_asked, threshold
        ):
            break
        st.asked_questions.pop()
    # Запись в records (как в детерминированном generate_question_node)
    st.records.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "question_id": card.question_id,
        "question": card.question,
        "options": card.options,
        "answer_type": card.answer_type,
        "difficulty": card.difficulty,
        "topic": card.topic,
        "section": st.current_section,
        "student_answer": None,
        "score01": None,
        "correct": None,
        "feedback": None,
        "model_used": None,
        "judge_score": None,
    })
    st = st.model_copy(update={"current_question": card})
    return _ok(question_id=card.question_id, question=card.question, options=card.options,
               answer_type=card.answer_type, topic=card.topic), st


def explain_error(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Объяснение ошибки с цитатой §N."""

    st = ctx.state
    question = str(args.get("question") or "")
    answer = str(args.get("answer") or "")
    context = [r.chunk.text for r in _rag_results(ctx, question, k=3)] or ["Нет контекста по теме."]
    result = tutor_mod.explain_error(question, answer, context, st, llm_call=ctx.llm_call, on_token=ctx.on_token)
    return _ok(text=result["text"], citation=result["citation"]), st


def evaluate_answer(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Оценить ответ ученика по текущему вопросу (единая логика с детерминированным узлом)."""
    from .evaluation import evaluate_and_record

    st = ctx.state
    card = st.current_question
    if card is None:
        return _err("Нет активного вопроса квиза"), st
    answer = str(args.get("answer") or st.pending_answer or "")
    st, message, _judge, explanation = evaluate_and_record(st, ctx.deps, card, answer)
    payload = {"message": message, "quiz_complete": st.quiz_complete}
    if st.records:
        rec = st.records[-1]
        payload.update({
            "score01": rec.get("score01"),
            "correct": rec.get("correct"),
            "feedback": rec.get("feedback"),
            "citation": explanation.get("citation") if explanation else None,
        })
    return _ok(**payload), st


def deep_dive(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Глубокий разбор: multi-chunk синтез экспертной моделью."""
    st = ctx.state
    topic = str(args.get("topic") or _active_topic_title(st) or st.topic or st.subject or "общая тема")
    context = [r.chunk.text for r in _rag_results(ctx, topic, k=8)] or ["Нет контекста по теме."]
    text = tutor_mod.generate_deep_dive(topic, context, st, llm_call=ctx.llm_call, on_token=ctx.on_token)
    st = st.model_copy(update={"lesson_text": text, "lesson_done": True})
    return _ok(topic=topic, text=text), st


def route_to_source(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Делегировать в source_fsm: нужны материалы (загрузка/OCR/веб-поиск).

    Возвращает управляющий сигнал; agent_loop передаёт управление в детерминированный
    субграф source_fsm и дожидается его завершения.
    """
    st = ctx.state
    return _ok(action="route_to_source", reason=args.get("reason") or ""), st


def finish_session(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Завершение сессии: суммаризация + экспорт (детерминированно в summary_node)."""
    st = ctx.state.model_copy(update={"quiz_complete": True, "session_status": "completed"})
    return _ok(action="finish_session", answered=st.answered_count, correct=st.correct_count), st


AGENT_TOOLS: Dict[str, Callable[[Dict[str, Any], AgentToolContext], Tuple[str, TutorState]]] = {
    "interview_progress": interview_progress,
    "set_intake": set_intake,
    "extract_intake_fields": lambda args, ctx: (_ok(fields=extract_intake_fields(str(args.get("text") or ""))), ctx.state),
    "build_intake_card": build_intake_card,
    "rag_search": rag_search,
    "get_knowledge_graph": get_knowledge_graph,
    "generate_lesson": generate_lesson,
    "generate_quiz": generate_quiz,
    "explain_error": explain_error,
    "evaluate_answer": evaluate_answer,
    "deep_dive": deep_dive,
    "route_to_source": route_to_source,
    "finish_session": finish_session,
}


def _param(**props: Any) -> Dict[str, Any]:
    return props


TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "interview_progress",
        "description": "Показать прогресс чек-листа: какие поля заполнены, какие пусты, следующий вопрос.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "set_intake",
        "description": "Записать значение поля чек-листа (learner_type, grade, subject, topic, has_textbook, mode). Значение нормализуется детерминированно.",
        "parameters": {"type": "object",
                      "properties": {"field": _param(type="string", description="поле чек-листа"),
                                     "value": _param(type="string", description="ответ ученика")},
                      "required": ["field", "value"]}}},
    {"type": "function", "function": {
        "name": "extract_intake_fields",
        "description": "Извлечь несколько полей чек-листа из свободного ответа ученика (например: 'хочу урок по дробям, учебника нет').",
        "parameters": {"type": "object",
                      "properties": {"text": _param(type="string", description="ответ ученика")},
                      "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "build_intake_card",
        "description": "Быстрое знакомство: показать ученику карточку-форму (имя, тип, класс, предмет, тема, учебник, режим) вместо пошаговых вопросов. Вызови в начале интервью, чтобы не затягивать intake.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "rag_search",
        "description": "Семантический поиск по учебнику (фильтр: предмет/класс/раздел). Вызови перед генерацией урока, вопроса, объяснения или ответом на вопрос ученика.",
        "parameters": {"type": "object",
                      "properties": {"query": _param(type="string", description="поисковый запрос"),
                                     "k": _param(type="integer", description="число фрагментов (по умолчанию 5)")},
                      "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_knowledge_graph",
        "description": "Граф знаний учебника: темы/уроки и связи (part_of/prerequisite/related). Для выбора темы и глубокого разбора.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "generate_lesson",
        "description": "Режим «урок»: объяснить тему по контексту учебника.",
        "parameters": {"type": "object",
                      "properties": {"topic": _param(type="string", description="тема (если не задана активная)")},
                      "required": []}}},
    {"type": "function", "function": {
        "name": "generate_quiz",
        "description": "Режим «квиз»: сгенерировать следующий вопрос (с антидубликатом).",
        "parameters": {"type": "object",
                      "properties": {"topic": _param(type="string", description="тема"),
                                     "difficulty": _param(type="string", enum=["easy", "medium", "hard"])},
                      "required": []}}},
    {"type": "function", "function": {
        "name": "explain_error",
        "description": "Объяснить ошибку ученика с цитатой из учебника (§N).",
        "parameters": {"type": "object",
                      "properties": {"question": _param(type="string"), "answer": _param(type="string")},
                      "required": ["question", "answer"]}}},
    {"type": "function", "function": {
        "name": "evaluate_answer",
        "description": "Оценить ответ ученика на текущий вопрос квиза (обновляет records, мастерство, сложность).",
        "parameters": {"type": "object",
                      "properties": {"answer": _param(type="string", description="ответ ученика")},
                      "required": ["answer"]}}},
    {"type": "function", "function": {
        "name": "deep_dive",
        "description": "Режим «глубокий разбор»: развёрнутый синтез по нескольким разделам учебника.",
        "parameters": {"type": "object",
                      "properties": {"topic": _param(type="string", description="тема")},
                      "required": []}}},
    {"type": "function", "function": {
        "name": "route_to_source",
        "description": "Нет материалов/контекста — делегировать в детерминированный субграф источника (загрузка учебника, OCR, веб-поиск).",
        "parameters": {"type": "object",
                      "properties": {"reason": _param(type="string", description="почему нужны материалы")},
                      "required": []}}},
    {"type": "function", "function": {
        "name": "finish_session",
        "description": "Завершить сессию: суммаризация результатов + экспорт.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]


def execute_agent_tool(
    name: str,
    arguments: Dict[str, Any],
    ctx: AgentToolContext,
) -> Tuple[str, TutorState]:
    """Выполнение агентного инструмента по имени. Возвращает (результат, состояние).

    Неизвестный инструмент → ошибка (модель получает {ok:false} и решает, что дальше).
    """
    fn = AGENT_TOOLS.get(name)
    if fn is None:
        return _err(f"Неизвестный инструмент: {name}"), ctx.state
    try:
        return fn(arguments or {}, ctx)
    except Exception as exc:  # прагматично: инструмент не должен ронять цикл
        return _err(f"Ошибка инструмента {name}: {exc}"), ctx.state
