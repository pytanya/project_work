"""
EduTutor — граф агента (LangGraph, раздел 2.2).

Условные рёбра: validate_intake (intake), route_source, route_textbook_result,
route_tutor. Узлы: intake, source (process_document / find_textbook / index /
source_failed), tutoring (generate_question / evaluate_answer / summary).

Исполнение: последовательные invoke с переносом состояния (для консольного MVP);
checkpointer (AsyncSqliteSaver) — опционально (расширение, раздел 8.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from langgraph.graph import END, START, StateGraph

from . import source_finder, tutor as tutor_mod
from .config import settings as default_settings
from .curriculum import grade_curriculum
from .intake import INTAKE_QUESTIONS, CHECKLIST_ORDER, apply_answer, compute_missing, validate_intake
from .judge import judge_evaluation
from .knowledge import Embedder, VectorStore, _make_chunks, make_embedder, make_store, process_document
from .states import TutorState

logger = logging.getLogger("edututor.graph")

NODE_SOURCE_ENTRY = "source_entry"
NODE_PROCESS_DOCUMENT = "process_document"
NODE_FIND_TEXTBOOK = "find_textbook"
NODE_SOURCE_FAILED = "source_failed"
NODE_TUTOR_NEXT = "tutor_next"
NODE_GENERATE_QUESTION = "generate_question"
NODE_EVALUATE_ANSWER = "evaluate_answer"
NODE_SUMMARY = "summary"


@dataclass
class GraphDeps:
    """Зависимости графа (инъекция для тестов)."""

    embedder: Embedder
    store: VectorStore
    tutor_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    eval_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    expert_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    judge_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    http: Optional[httpx.Client] = None
    settings: Any = None
    collection_name: str = "edututor"
    source_collector: Optional[Callable[..., Any]] = None  # override find_textbook
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None  # (event, data)


def make_graph_deps(settings: Any = None) -> GraphDeps:
    """Стандартные зависимости (реальные embedder/хранилище, LLM-клиенты по умолчанию)."""
    s = settings or default_settings
    embedder = make_embedder(s)
    store = make_store("edututor", embedder, persist_dir=Path(s.CHROMA_PERSIST_DIR), settings=s)
    return GraphDeps(embedder=embedder, store=store, settings=s)


def _rag_context(store: VectorStore, query: str, state: TutorState, k: int = 3) -> List[str]:
    filters: Dict[str, Any] = {}
    if state.subject:
        filters["subject"] = state.subject
    if state.grade:
        filters["grade"] = state.grade
    results = store.search(query, k=k, filters=filters or None)
    return [r.chunk.text for r in results]


def _emit(deps: GraphDeps, event: str, **data: Any) -> None:
    if deps.on_event is not None:
        try:
            deps.on_event(event, data)
        except Exception:  # pragma: no cover — публикация не должна ронять граф
            logger.warning("on_event(%s) упал", event)


# ----------------------------------------------------------------------
# Узлы
# ----------------------------------------------------------------------
def intake_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)

    # Применяем ответ на текущий вопрос чек-листа.
    # Если поле не задано, но intake ещё не завершён — сам определяем первое
    # недостающее поле (удобно для API: первый ответ без привязки к полю).
    if st.pending_answer is not None:
        if st.intake_field is None and compute_missing(st):
            for field_name in CHECKLIST_ORDER:
                if field_name in compute_missing(st):
                    st.intake_field = field_name
                    break
        if st.intake_field:
            st = apply_answer(st, st.intake_field, st.pending_answer)
            st.intake_field = None
            st.pending_answer = None

    decision = validate_intake(st, max_iterations=deps.settings.MAX_INTAKE_ITERATIONS)
    if decision.decision == "ask":
        for field_name in CHECKLIST_ORDER:
            if field_name in decision.missing_fields:
                st.intake_field = field_name
                st.agent_question = INTAKE_QUESTIONS[field_name]
                st.agent_message = None
                break
        _emit(deps, "intake.question",
              question=st.agent_question, missing_fields=decision.missing_fields)
        return st.model_dump()

    if decision.decision == "emergency_start":
        st.agent_message = decision.warning
        _emit(deps, "system", message=decision.warning, kind="intake.warning")

    # grade_curriculum: сверка темы с ФГОС (В-8)
    if st.subject and st.topic and not st.curriculum:
        cur = grade_curriculum(st.subject, st.grade, st.topic, ref_dir=deps.settings.FGOS_REFERENCE_DIR)
        if cur.fgos_code:
            st.curriculum = cur.fgos_code
        else:
            st.curriculum = "unverified"
            if not st.agent_message:
                st.agent_message = cur.warning

    return st.model_dump()


def after_intake(state: TutorState) -> str:
    if state.intake_field:
        return END
    return NODE_SOURCE_ENTRY


def source_entry(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    if st.sources or st.collection_id:
        st.source_status = "ready"
        return st.model_dump()
    return {}


def route_source(state: TutorState) -> str:
    if state.sources or state.collection_id or state.source_status == "ready":
        return NODE_TUTOR_NEXT
    if state.textbook_file:
        return NODE_PROCESS_DOCUMENT
    return NODE_FIND_TEXTBOOK


def process_document_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    if not st.textbook_file:
        return {"source_status": "failed", "source_note": "no file"}
    path = Path(st.textbook_file)
    _emit(deps, "source.progress", stage="index", url="", status="indexing",
          message=f"Разбор документа {path.name}…")
    stats = process_document(
        path, source=path.name, store=deps.store, subject=st.subject, grade=st.grade
    )
    st.collection_id = stats["collection"]
    st.source_status = "ready"
    st.sources = [{"type": "file", "path": str(path), "num_chunks": stats["num_chunks"]}]
    st.source_note = f"Документ проиндексирован: {stats['num_chunks']} чанков"
    _emit(deps, "source.progress", stage="index", url="", status="done",
          message=st.source_note)
    return st.model_dump()


def find_textbook_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    if st.sources:
        return st.model_dump()

    _emit(deps, "source.progress", stage="catalog", url="", status="searching",
          message=f"Поиск материалов по теме «{st.topic or st.subject or ''}»…")
    col = (deps.source_collector or source_finder.collect_source_materials)(
        subject=st.subject or "",
        topic=st.topic or "",
        grade=st.grade or "",
        author=st.textbook_author or "",
        settings=deps.settings,
        http=deps.http,
    )
    if col.status == "ready":
        local_pdf = [s for s in col.sources if s.get("type") == "local_pdf"]
        if local_pdf:
            st.textbook_file = local_pdf[0]["path"]
            st.sources = col.sources
            st.source_status = "ready"
            st.source_note = col.message
            _emit(deps, "source.progress", stage="verify", url="", status="found",
                  message=col.message)
            return st.model_dump()
        # материалы по теме → индексация
        _emit(deps, "source.progress", stage="index", url="", status="indexing",
              message=f"Индексация материалов: {len(col.sources)} источников…")
        chunks: List[Any] = []
        for s, t in zip(col.sources, col.texts):
            chunks.extend(
                _make_chunks(t, source=s.get("url", "web"), subject=st.subject, grade=st.grade)
            )
        deps.store.add(chunks)
        st.collection_id = "web"
        st.sources = col.sources
        st.source_status = "ready"
        st.source_note = f"Собрано материалов: {len(col.sources)} источников"
        _emit(deps, "source.progress", stage="index", url="", status="done",
              message=st.source_note)
        return st.model_dump()

    st.source_status = "failed"
    st.source_note = col.failed_reason or col.message
    st.agent_message = col.message or "Материалы по теме не найдены."
    _emit(deps, "source.failed", reason=st.source_note, message=st.agent_message)
    return st.model_dump()


def route_textbook_result(state: TutorState) -> str:
    if state.source_status == "failed":
        return NODE_SOURCE_FAILED
    if state.textbook_file:
        return NODE_PROCESS_DOCUMENT
    return NODE_TUTOR_NEXT


def source_failed_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    st.session_status = "failed"
    st.agent_message = st.agent_message or "Материалы по теме не найдены. Предлагаем загрузить свой документ."
    return st.model_dump()


def route_tutor(state: TutorState) -> str:
    if state.quiz_complete or state.session_status == "failed":
        return NODE_SUMMARY
    if state.current_question is None:
        return NODE_GENERATE_QUESTION
    if state.pending_answer is not None:
        return NODE_EVALUATE_ANSWER
    return NODE_GENERATE_QUESTION


def generate_question_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    topic = st.topic or st.subject or "общая тема"
    context = _rag_context(deps.store, topic, st, k=3)
    if not context:
        context = ["Нет контекста по теме."]
    card = tutor_mod.generate_question(
        topic, context, st.difficulty, st, llm_call=deps.tutor_llm
    )
    st.current_question = card
    st.agent_question = card.question
    st.agent_options = card.options
    # НЕ обнуляем agent_message: там может быть фидбек предыдущей оценки
    _emit(deps, "quiz.card", question_id=card.question_id, question=card.question,
          options=card.options, answer_type=card.answer_type, difficulty=card.difficulty,
          topic=card.topic)
    return st.model_dump()


def evaluate_answer_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    card = st.current_question
    answer = st.pending_answer or ""
    context = _rag_context(deps.store, card.topic if card else "", st, k=3)
    if not context:
        context = ["Нет контекста по теме."]

    graded = tutor_mod.evaluate_answer(
        card.question, answer, context, st, llm_call=deps.eval_llm
    )
    tutor_mod.update_knowledge_map(st, card.topic, graded.score)
    new_difficulty = tutor_mod.adjust_difficulty(st, graded.correct)

    # Судья: контракт «оценка ответа ученика» (К-4)
    judge_result = judge_evaluation(
        card.question,
        answer,
        {"score": graded.score, "correct": graded.correct, "feedback": graded.feedback},
        judge_call=deps.judge_llm,
    )
    st.last_judge_score = judge_result.avg_score

    message = f"{'Верно' if graded.correct else 'Ошибка'} (оценка {round(graded.score * 10, 1)}/10)."
    if graded.feedback:
        message += f" {graded.feedback}"
    if not graded.correct:
        explanation = tutor_mod.explain_error(
            card.question, answer, context, st, llm_call=deps.expert_llm
        )
        message += f"\nОбъяснение: {explanation['text']}"
        if explanation["citation"]["paragraph"]:
            message += f"\nЦитата: {explanation['citation']['paragraph']}"
    st.agent_message = message
    st.current_question = None
    st.pending_answer = None

    _emit(deps, "tutor.explanation" if not graded.correct else "system",
          message=message, citation=explanation["citation"] if not graded.correct else None)

    if st.answered_count >= st.num_questions:
        st.quiz_complete = True
        st.session_status = "completed"

    return st.model_dump()


def summary_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    total = st.answered_count
    correct = st.correct_count
    km = {k: round(v, 2) for k, v in st.knowledge_map.items()}
    st.summary_text = (
        f"Квиз завершён. Правильных ответов: {correct}/{total}. "
        f"Карта знаний: {km}"
    )
    # Не затираем фидбек последней оценки (объяснение ошибки)
    if st.agent_message:
        st.agent_message = f"{st.agent_message}\n\n{st.summary_text}"
    else:
        st.agent_message = st.summary_text
    st.session_status = "completed"
    _emit(deps, "tutor.summary", correct=st.correct_count, total=st.answered_count,
          knowledge_map={k: round(v, 2) for k, v in st.knowledge_map.items()})
    return st.model_dump()


# ----------------------------------------------------------------------
# Сборка графа
# ----------------------------------------------------------------------
def build_graph(deps: Optional[GraphDeps] = None, checkpointer: Any = None) -> Any:
    deps = deps or make_graph_deps()

    g = StateGraph(TutorState)

    g.add_node("intake_node", lambda s: intake_node(s, deps))
    g.add_node(NODE_SOURCE_ENTRY, lambda s: source_entry(s, deps))
    g.add_node(NODE_PROCESS_DOCUMENT, lambda s: process_document_node(s, deps))
    g.add_node(NODE_FIND_TEXTBOOK, lambda s: find_textbook_node(s, deps))
    g.add_node(NODE_SOURCE_FAILED, lambda s: source_failed_node(s, deps))
    g.add_node(NODE_TUTOR_NEXT, lambda s: {})
    g.add_node(NODE_GENERATE_QUESTION, lambda s: generate_question_node(s, deps))
    g.add_node(NODE_EVALUATE_ANSWER, lambda s: evaluate_answer_node(s, deps))
    g.add_node(NODE_SUMMARY, lambda s: summary_node(s, deps))

    g.add_edge(START, "intake_node")
    g.add_conditional_edges("intake_node", after_intake, {END: END, NODE_SOURCE_ENTRY: NODE_SOURCE_ENTRY})
    g.add_conditional_edges(
        NODE_SOURCE_ENTRY,
        route_source,
        {
            NODE_TUTOR_NEXT: NODE_TUTOR_NEXT,
            NODE_PROCESS_DOCUMENT: NODE_PROCESS_DOCUMENT,
            NODE_FIND_TEXTBOOK: NODE_FIND_TEXTBOOK,
        },
    )
    g.add_conditional_edges(
        NODE_FIND_TEXTBOOK,
        route_textbook_result,
        {
            NODE_SOURCE_FAILED: NODE_SOURCE_FAILED,
            NODE_PROCESS_DOCUMENT: NODE_PROCESS_DOCUMENT,
            NODE_TUTOR_NEXT: NODE_TUTOR_NEXT,
        },
    )
    g.add_edge(NODE_PROCESS_DOCUMENT, NODE_TUTOR_NEXT)
    g.add_edge(NODE_SOURCE_FAILED, END)
    g.add_conditional_edges(
        NODE_TUTOR_NEXT,
        route_tutor,
        {
            NODE_GENERATE_QUESTION: NODE_GENERATE_QUESTION,
            NODE_EVALUATE_ANSWER: NODE_EVALUATE_ANSWER,
            NODE_SUMMARY: NODE_SUMMARY,
        },
    )
    g.add_edge(NODE_GENERATE_QUESTION, END)
    g.add_edge(NODE_EVALUATE_ANSWER, NODE_TUTOR_NEXT)
    g.add_edge(NODE_SUMMARY, END)

    return g.compile(checkpointer=checkpointer)
