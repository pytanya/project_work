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
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from langgraph.graph import END, START, StateGraph

from . import source_finder, tutor as tutor_mod
from .config import settings as default_settings
from .curriculum import grade_curriculum
from .intake import INTAKE_QUESTIONS, CHECKLIST_ORDER, apply_answer, compute_missing, validate_intake
from .judge import judge_evaluation
from .knowledge import (
    Embedder,
    VectorStore,
    _make_chunks,
    detect_text_layer,
    make_collection_name,
    make_embedder,
    make_store,
    parse_document,
    process_document,
)
from .knowledge_graph import build_or_load_textbook_graph, build_textbook_graph
from .states import TutorState

logger = logging.getLogger("edututor.graph")

NODE_SOURCE_ENTRY = "source_entry"
NODE_PROCESS_DOCUMENT = "process_document"
NODE_FIND_TEXTBOOK = "find_textbook"
NODE_SOURCE_FAILED = "source_failed"
NODE_WAIT_FOR_UPLOAD = "wait_for_upload"
NODE_TOPIC_GATE = "topic_gate"
NODE_LESSON = "lesson_node"
NODE_ASK_PAGE_RANGE = "ask_page_range"
NODE_HANDLE_DOC_PAGES = "handle_doc_pages"
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
    collection = make_collection_name(embedder)
    store = make_store(collection, embedder, persist_dir=Path(s.CHROMA_PERSIST_DIR), settings=s)
    return GraphDeps(embedder=embedder, store=store, settings=s, collection_name=collection)


def _rag_chunks(store: VectorStore, query: str, state: TutorState, k: int = 3) -> List[Any]:
    """RAG-поиск с метаданными (нужно для section/параграфа в экспорте)."""
    from .knowledge import SearchResult

    filters: Dict[str, Any] = {}
    if state.subject:
        filters["subject"] = state.subject
    if state.grade:
        filters["grade"] = state.grade
    # Подготовка по теме: фильтр по разделу активного узла графа
    if state.active_topic:
        section = _active_topic_section(state)
        if section:
            filters["section_number"] = section
    results: List[SearchResult] = store.search(query, k=k, filters=filters or None)
    return results


def _active_topic_section(state: TutorState) -> Optional[str]:
    """Номер раздела активной темы из графа знаний (если узел — секция)."""
    if not state.knowledge_graph or not state.active_topic:
        return None
    for n in state.knowledge_graph.get("nodes", []):
        if n.get("id") == state.active_topic:
            return n.get("section_number")
    return None


def _rag_context(store: VectorStore, query: str, state: TutorState, k: int = 3) -> List[str]:
    return [r.chunk.text for r in _rag_chunks(store, query, state, k)]


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
        return NODE_TOPIC_GATE
    if state.textbook_file:
        return NODE_PROCESS_DOCUMENT
    if state.has_textbook is True:
        # «да, есть учебник», но файл ещё не загружен — ждём загрузку, а не веб-поиск
        return NODE_WAIT_FOR_UPLOAD
    return NODE_FIND_TEXTBOOK


def _match_topic(kg: Any, text: str) -> Optional[str]:
    """Матчит ответ ученика с узлом графа: «урок N» или подстрока названия."""
    t = (text or "").strip().lower()
    if not t:
        return None
    nodes = kg.to_dict()["nodes"]
    m = re.match(r"урок\s*(\d{1,3})", t)
    if m:
        num = m.group(1)
        for n in nodes:
            title = n.get("title", "").lower()
            if (n.get("id", "").endswith(f":{num}")
                    or title.startswith(f"урок {num}")
                    or title.startswith(f"урок {num}:")):
                return n["id"]
    for n in nodes:
        if t in n.get("title", "").lower():
            return n["id"]
    # Вырожденный граф (нет заголовков разделов): единственная тема = вся тема книги
    others = [n for n in nodes if n.get("type") not in ("book",)]
    if len(others) == 1:
        return others[0]["id"]
    return None


def _node_title(kg: Any, node_id: str) -> str:
    for n in kg.to_dict()["nodes"]:
        if n.get("id") == node_id:
            return n.get("title", node_id)
    return node_id


def topic_gate_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Гейт выбора темы: после индексации ждём «какую тему изучаем», а не авто-квиз."""
    from .knowledge_graph import KnowledgeGraph

    st = state.model_copy(deep=True)
    if not st.awaiting_topic:
        return st.model_dump()

    if st.pending_answer is not None:
        answer = (st.pending_answer or "").strip()
        st.pending_answer = None
        low = answer.lower()
        if low in ("отмена", "cancel", "выйти", "не надо", "перейти к квизу", "без темы"):
            st.active_topic = None
            st.awaiting_topic = False
            st.agent_question = None
            st.agent_message = "Ок, готовим квиз по всему учебнику."
            _emit(deps, "system", message=st.agent_message, kind="topic.all")
            return st.model_dump()
        if low in ("все", "весь учебник", "всё", "вся", "все темы"):
            st.active_topic = None
            st.awaiting_topic = False
            st.agent_question = None
            st.agent_message = "Готовим квиз по всему учебнику."
            _emit(deps, "system", message=st.agent_message, kind="topic.all")
            return st.model_dump()
        kg = KnowledgeGraph.from_dict(st.knowledge_graph or {})
        node_id = _match_topic(kg, answer)
        if node_id:
            st.active_topic = node_id
            st.awaiting_topic = False
            st.agent_question = None
            title = _node_title(kg, node_id)
            st.agent_message = f"Тема выбрана: {title}. Готовимся!"
            _emit(deps, "system", message=st.agent_message, kind="topic.selected")
            return st.model_dump()
        st.agent_question = "Не нашёл такую тему. Выбери из «Темы учебника» слева или напиши «Урок N» / название."
        return st.model_dump()

    st.agent_question = (
        "Учебник проиндексирован. Какую тему изучаем? Выбери из «Темы учебника» слева "
        "или назови урок (например: «Урок 5») / напиши «все» для всего учебника."
    )
    if not st.agent_message:
        st.agent_message = "Выберите тему для подготовки."
    _emit(deps, "intake.question", question=st.agent_question, missing_fields=["topic"])
    return st.model_dump()


def route_after_topic_gate(state: TutorState) -> str:
    """После гейта: выбрана тема → урок (если режим lesson) или квиз; иначе ждём (END)."""
    if state.awaiting_topic:
        return END
    if state.mode == "lesson" and not state.lesson_confirmed:
        return NODE_LESSON
    return NODE_TUTOR_NEXT


def lesson_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Режим «урок»: объясняем тему по RAG-контексту, затем спрашиваем «готов к квизу?»."""
    st = state.model_copy(deep=True)
    if st.lesson_confirmed:
        return st.model_dump()

    if st.pending_answer is not None:
        low = (st.pending_answer or "").strip().lower()
        st.pending_answer = None
        if low in ("да", "yes", "у", "готов", "конечно", "начинаем", "квиз", "поехали", "всё"):
            st.lesson_confirmed = True
            st.lesson_done = True
            st.agent_question = None
            _emit(deps, "system", message="Отлично! Начинаем квиз.", kind="lesson.done")
            return st.model_dump()
        # «нет»/повтор → сбрасываем и перегенерируем урок ниже
        st.lesson_done = False
        st.lesson_text = None
        st.agent_question = None
        _emit(deps, "system", message="Повторяем урок по теме.", kind="lesson.repeat")

    if st.lesson_text:
        # урок уже показан — ждём подтверждения
        st.lesson_done = True
        st.agent_question = "Готов(а) перейти к квизу? (да / нет)"
        _emit(deps, "intake.question", question=st.agent_question, missing_fields=["lesson_confirm"])
        return st.model_dump()

    # Генерируем урок по активной теме
    topic = st.topic or st.subject or "общая тема"
    if st.active_topic:
        from .knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph.from_dict(st.knowledge_graph or {})
        title = _node_title(kg, st.active_topic)
        if title:
            topic = title
    chunks = _rag_chunks(deps.store, topic, st, k=5)
    context = [c.chunk.text for c in chunks] or ["Нет контекста по теме."]
    st.lesson_text = tutor_mod.generate_lesson(topic, context, st, llm_call=deps.tutor_llm)
    st.lesson_done = True
    _emit(deps, "tutor.lesson", text=st.lesson_text, topic=topic)
    st.agent_message = "Урок по теме готов. Можно задать вопрос или перейти к квизу."
    _emit(deps, "system", message=st.agent_message, kind="lesson.ready")
    return st.model_dump()


def route_after_lesson(state: TutorState) -> str:
    """После урока: подтверждён переход к квизу → квиз; иначе ждём (END)."""
    if state.lesson_confirmed:
        return NODE_TUTOR_NEXT
    return END


def wait_for_upload_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Узел «загрузите учебник»: has_textbook=True, файла нет — ждём upload."""
    st = state.model_copy(deep=True)
    st.agent_question = (
        "Загрузите, пожалуйста, файл учебника (PDF/DOCX) — перетащите его в блок "
        "«Загрузить учебник» слева, или нажмите «Найти учебник», если файла нет."
    )
    if not st.agent_message:
        st.agent_message = "Учебник указан, но файл не загружен."
    _emit(deps, "intake.question", question=st.agent_question, missing_fields=["textbook_file"])
    return st.model_dump()


def process_document_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    if st.textbook_scanned:
        # уже знаем, что это скан — ждём/обрабатываем страницы в других узлах
        return st.model_dump()
    if not st.textbook_file:
        return {"source_status": "failed", "source_note": "no file"}
    path = Path(st.textbook_file)
    source_name = st.textbook_name or path.name
    _emit(deps, "source.progress", stage="index", url="", status="indexing",
          message=f"Разбор документа {source_name}…")
    text = parse_document(path)

    if detect_text_layer(text, min_chars=deps.settings.OCR_MIN_TEXT_CHARS):
        st.textbook_scanned = True
        st.agent_question = (
            "Учебник сканированный (без текста). Открой учебник и укажи страницы нужной "
            "темы и саму тему (например: 12-15, Атмосфера). Или напиши «все» для полного распознавания."
        )
        st.agent_message = "Файл не содержит текстового слоя — распознаю по страницам."
        _emit(deps, "system", message=st.agent_message, kind="doc.scanned")
        return st.model_dump()

    stats = process_document(
        path, source=source_name, store=deps.store, subject=st.subject, grade=st.grade
    )
    st.collection_id = stats["collection"]
    st.source_status = "ready"
    st.sources = [{"type": "file", "path": str(path), "num_chunks": stats["num_chunks"]}]
    st.source_note = f"Документ проиндексирован: {stats['num_chunks']} чанков"
    st.knowledge_graph = build_or_load_textbook_graph(
        text, source=source_name, path=path, graph_dir=deps.settings.KNOWLEDGE_GRAPH_DIR
    ).to_dict()
    st.awaiting_topic = True
    _emit(deps, "source.progress", stage="index", url="", status="done",
          message=st.source_note)
    _emit(deps, "graph.ready", nodes=len(st.knowledge_graph.get("nodes", [])),
          edges=len(st.knowledge_graph.get("edges", [])))
    return st.model_dump()


def route_doc_result(state: TutorState) -> str:
    """Маршрут после process_document: скан → запрос страниц / обработка; индекс готов → гейт темы."""
    if not state.textbook_scanned:
        return NODE_TOPIC_GATE
    if state.textbook_pages is None and state.pending_answer is not None:
        return NODE_HANDLE_DOC_PAGES
    return NODE_ASK_PAGE_RANGE


def ask_page_range_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Узел «открой учебник и назови страницы + тему» (цикл убеждения, 3.2)."""
    st = state.model_copy(deep=True)
    if st.textbook_pages is not None:
        return st.model_dump()
    if st.doc_pages_attempts >= deps.settings.OCR_MAX_ATTEMPTS:
        st.agent_question = "Напиши «все» для полного распознавания (долго) или «отмена»."
        st.agent_message = "Не удалось получить страницы. Полный OCR может занять много времени."
    else:
        st.agent_question = (
            "Пожалуйста, открой учебник и посмотри: 1) номера страниц нужной темы, "
            "2) название темы/урока. Ответь, например: «12-15, Атмосфера»."
        )
        if not st.agent_message:
            st.agent_message = "Учебник сканированный — нужны страницы для распознавания."
    _emit(deps, "intake.question", question=st.agent_question, missing_fields=["textbook_pages"])
    return st.model_dump()


def handle_doc_pages_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Обработка ответа «страницы + тема»: parse → оффсет/буфер → OCR → валидация → индекс."""
    from .knowledge import _make_chunks, detect_page_offset, ocr_pages, pdf_page_count, validate_topic_in_text
    from .nlp import parse_doc_request

    st = state.model_copy(deep=True)
    answer = (st.pending_answer or "").strip()
    st.pending_answer = None

    if answer.lower() in ("отмена", "cancel", "не надо"):
        st.session_status = "failed"
        st.agent_message = "OCR отменён. Можешь загрузить учебник с текстом или выбрать источник."
        return st.model_dump()

    path = Path(st.textbook_file)
    num_pages = pdf_page_count(path)
    req = parse_doc_request(answer, num_pages)

    if not req.ok:
        st.doc_pages_attempts += 1
        st.agent_message = None
        return st.model_dump()  # ask_page_range_node переспросит (с учётом attempts)

    # диапазон страниц
    if req.all_pages:
        phys_start, phys_end = 1, num_pages
    else:
        offset = st.page_offset
        if offset is None:
            offset = detect_page_offset(path) if deps.settings.OCR_DETECT_PAGE_NUMBERS else None
            st.page_offset = offset or 0
        buffer = deps.settings.OCR_PAGE_BUFFER
        phys_start = max(1, req.pages[0] - (offset or 0) - buffer)
        phys_end = min(num_pages, req.pages[1] - (offset or 0) + buffer)
    if phys_end - phys_start + 1 > deps.settings.OCR_MAX_PAGES:
        phys_end = phys_start + deps.settings.OCR_MAX_PAGES - 1

    st.agent_message = f"Распознаю страницы {phys_start}-{phys_end}…"
    _emit(deps, "source.progress", stage="ocr", url="", status="indexing",
          message=st.agent_message)
    ocr = ocr_pages(path, (phys_start, phys_end))
    text = ocr["text"]

    if req.topic and not validate_topic_in_text(req.topic, text):
        st.doc_pages_attempts += 1
        st.agent_question = (
            f"В страницах {phys_start}-{phys_end} не нашёл тему «{req.topic}». "
            "Возможно, страницы указаны неверно. Уточни страницы и тему, пожалуйста."
        )
        st.agent_message = None
        return st.model_dump()

    source_name = st.textbook_name or path.name
    chunks = _make_chunks(text, source=source_name, subject=st.subject, grade=st.grade)
    offset = st.page_offset or 0
    printed_start = phys_start + offset
    printed_end = phys_end + offset
    for chunk in chunks:
        chunk.page_number = f"{printed_start}-{printed_end}"
    deps.store.add(chunks)

    st.collection_id = "ocr"
    st.source_status = "ready"
    st.sources = [{"type": "ocr", "path": str(path), "pages": [phys_start, phys_end],
                   "num_chunks": len(chunks), "page_offset": offset}]
    st.source_note = f"OCR страниц {phys_start}-{phys_end}: {len(chunks)} чанков"
    st.knowledge_graph = build_or_load_textbook_graph(
        text, source=source_name, path=path, graph_dir=deps.settings.KNOWLEDGE_GRAPH_DIR
    ).to_dict()
    st.awaiting_topic = True
    st.textbook_pages = answer
    st.textbook_topic = req.topic
    st.agent_message = None
    _emit(deps, "source.progress", stage="ocr", url="", status="done",
          message=st.source_note)
    return st.model_dump()


def route_after_handle(state: TutorState) -> str:
    """После обработки страниц: готово → гейт темы; иначе переспросить (ask_page_range)."""
    if state.source_status == "ready" or state.textbook_pages is not None:
        return NODE_TOPIC_GATE
    return NODE_ASK_PAGE_RANGE


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
    chunks = _rag_chunks(deps.store, topic, st, k=3)
    context = [c.chunk.text for c in chunks]
    if not context:
        context = ["Нет контекста по теме."]
    card = tutor_mod.generate_question(
        topic, context, st.difficulty, st, llm_call=deps.tutor_llm
    )
    st.current_question = card
    st.current_section = chunks[0].chunk.section_number if chunks else None
    st.agent_question = card.question
    st.agent_options = card.options
    # НЕ обнуляем agent_message: там может быть фидбек предыдущей оценки
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

    # Экспорт учителю: заполняем запись вопроса оценкой/судьёй
    if st.records and st.records[-1].get("question_id") == card.question_id:
        st.records[-1].update({
            "student_answer": answer,
            "score01": round(graded.score, 4),
            "correct": graded.correct,
            "feedback": graded.feedback,
            "model_used": graded.model_used,
            "judge_score": judge_result.avg_score,
        })

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
    g.add_node(NODE_WAIT_FOR_UPLOAD, lambda s: wait_for_upload_node(s, deps))
    g.add_node(NODE_TOPIC_GATE, lambda s: topic_gate_node(s, deps))
    g.add_node(NODE_LESSON, lambda s: lesson_node(s, deps))
    g.add_node(NODE_ASK_PAGE_RANGE, lambda s: ask_page_range_node(s, deps))
    g.add_node(NODE_HANDLE_DOC_PAGES, lambda s: handle_doc_pages_node(s, deps))
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
            NODE_TOPIC_GATE: NODE_TOPIC_GATE,
            NODE_PROCESS_DOCUMENT: NODE_PROCESS_DOCUMENT,
            NODE_FIND_TEXTBOOK: NODE_FIND_TEXTBOOK,
            NODE_WAIT_FOR_UPLOAD: NODE_WAIT_FOR_UPLOAD,
        },
    )
    g.add_conditional_edges(
        NODE_TOPIC_GATE,
        route_after_topic_gate,
        {END: END, NODE_LESSON: NODE_LESSON, NODE_TUTOR_NEXT: NODE_TUTOR_NEXT},
    )
    g.add_conditional_edges(
        NODE_LESSON,
        route_after_lesson,
        {END: END, NODE_TUTOR_NEXT: NODE_TUTOR_NEXT},
    )
    g.add_edge(NODE_WAIT_FOR_UPLOAD, END)
    g.add_conditional_edges(
        NODE_PROCESS_DOCUMENT,
        route_doc_result,
        {
            NODE_TOPIC_GATE: NODE_TOPIC_GATE,
            NODE_HANDLE_DOC_PAGES: NODE_HANDLE_DOC_PAGES,
            NODE_ASK_PAGE_RANGE: NODE_ASK_PAGE_RANGE,
        },
    )
    g.add_conditional_edges(
        NODE_HANDLE_DOC_PAGES,
        route_after_handle,
        {
            NODE_TOPIC_GATE: NODE_TOPIC_GATE,
            NODE_ASK_PAGE_RANGE: NODE_ASK_PAGE_RANGE,
        },
    )
    g.add_edge(NODE_ASK_PAGE_RANGE, END)
    g.add_conditional_edges(
        NODE_FIND_TEXTBOOK,
        route_textbook_result,
        {
            NODE_SOURCE_FAILED: NODE_SOURCE_FAILED,
            NODE_PROCESS_DOCUMENT: NODE_PROCESS_DOCUMENT,
            NODE_TUTOR_NEXT: NODE_TUTOR_NEXT,
        },
    )
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
