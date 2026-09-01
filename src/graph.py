"""
EduTutor — граф агента (LangGraph, раздел 2.2).

Условные рёбра: validate_intake (intake), route_source, route_textbook_result,
route_tutor. Узлы: intake, source (process_document / find_textbook / index /
source_failed), tutoring (generate_question / evaluate_answer / summary).

Исполнение: последовательные invoke с переносом состояния (для консольного MVP);
checkpointer (AsyncSqliteSaver) — опционально (расширение, раздел 8.4).
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from langgraph.graph import END, START, StateGraph

from . import adaptive, source_finder, tutor as tutor_mod
from .config import settings as default_settings
from .export import write_session_exports
from .okf import emit_okf_bundle
from .curriculum import grade_curriculum
from .intake import INTAKE_QUESTIONS, CHECKLIST_ORDER, apply_answer, compute_missing, extract_intake_fields, maybe_start_card, validate_intake
from .judge import judge_evaluation
from .knowledge import (
    Embedder,
    VectorStore,
    _clean_text_lines,
    _make_chunks,
    detect_text_layer,
    make_collection_name,
    make_embedder,
    make_store,
    parse_document,
    process_document,
)
from .knowledge_graph import PART_OF, build_or_load_textbook_graph, build_textbook_graph
from .observability import log_graph_node as _obs_log_node
from .states import TutorState
from api.schemas import QuizCard

logger = logging.getLogger("edututor.graph")

NODE_SOURCE_ENTRY = "source_entry"
NODE_PROCESS_DOCUMENT = "process_document"
NODE_FIND_TEXTBOOK = "find_textbook"
NODE_SOURCE_FAILED = "source_failed"
NODE_WAIT_FOR_UPLOAD = "wait_for_upload"
NODE_REUSE_GATE = "reuse_gate"
NODE_TOPIC_GATE = "topic_gate"
NODE_CONTENT = "content_node"
NODE_ASK_PAGE_RANGE = "ask_page_range"
NODE_HANDLE_DOC_PAGES = "handle_doc_pages"
NODE_TUTOR_NEXT = "tutor_next"
NODE_AGENT_TUTOR = "agent_tutor_node"
NODE_GENERATE_QUESTION = "generate_question"
NODE_EVALUATE_ANSWER = "evaluate_answer"
NODE_SUBTASK = "subtask"
NODE_SUMMARY = "summary"


def _extract_lesson_sources(
    rag_sources: List[Optional[Dict[str, Any]]],
    state_sources: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Извлекает уникальные источники для атрибуции урока.

    Собирает URL из метаданных RAG-чанков (поле ``source``) и из ``state.sources``
    (поле ``url``/``title``). Дедупликация по URL.
    Возвращает список ``[{url, title, domain}]`` для фронтенда.
    """
    seen: set = set()
    result: List[Dict[str, str]] = []
    for meta in rag_sources:
        if meta is None:
            continue
        url = (meta.get("source") or "").strip()
        if not url or url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        m = re.search(r"https?://([^/\s]+)", url)
        domain = m.group(1) if m else ""
        title = meta.get("section_title") or meta.get("title") or domain
        result.append({"url": url, "title": title, "domain": domain})
    # Дополняем из state.sources (веб-поиск), если URL не дублируются
    for s in (state_sources or []):
        url = (s.get("url") or "").strip()
        if not url or url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        m = re.search(r"https?://([^/\s]+)", url)
        domain = m.group(1) if m else ""
        title = s.get("title") or domain
        result.append({"url": url, "title": title, "domain": domain})
    return result


def _topic_count(nodes):
    """Count only non-book topics (matches frontend KnowledgeGraphPanel filtering)."""
    return sum(1 for n in (nodes or []) if n.get("type") != "book")


# ----------------------------------------------------------------------
# Кэш сгенерированных уроков (план доработки, блоки 3 и 7)
# ----------------------------------------------------------------------
def _load_cached_lesson(st: TutorState, deps: GraphDeps, topic: str):
    """Кэшированный урок из прошлого занятия по (student_id, subject, topic, grade).

    Возвращает Lesson или None. Кэш активен только в режиме «урок» и пока урок
    текущей сессии ещё не показан.
    """
    if st.mode != "lesson" or st.lesson_done or not topic:
        return None
    from .lesson_cache import load_lesson

    try:
        return load_lesson(
            deps.settings.SOURCES_CACHE_DIR,
            st.student_id or "", st.subject or "", topic, st.grade or "",
        )
    except Exception as exc:
        logger.warning("load_lesson: %s", exc)
        return None


def _build_web_knowledge_graph(sources: List[Dict[str, Any]], topic: str) -> Dict[str, Any]:
    """Построение минимального графа знаний из web-источников.

    Для web-поиска (без загруженного PDF) knowledge_graph не заполняется
    автоматически — эта функция создаёт структуру из URL/заголовков источников,
    чтобы при повторном прохождении темы фронтенд мог отрисовать панель.
    """
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    if topic:
        nodes.append({
            "id": f"topic_{topic}",
            "label": topic,
            "type": "topic",
        })
    for i, s in enumerate(sources):
        url = (s.get("url") or "").strip()
        title = (s.get("title") or s.get("domain") or url)[:100]
        domain = s.get("domain") or ""
        node_id = f"web_{i}"
        nodes.append({
            "id": node_id,
            "label": title,
            "type": "web_source",
            "metadata": {"url": url, "domain": domain},
        })
        if topic:
            edges.append({
                "source": f"topic_{topic}",
                "target": node_id,
                "label": "использован",
            })
    return {"nodes": nodes, "edges": edges}


def _save_lesson_to_cache(st: TutorState, deps: GraphDeps, lesson, topic: str) -> None:
    """Сохраняет сгенерированный урок в кэш для повторного прохождения."""
    if not topic or lesson is None:
        return
    from .lesson_cache import save_knowledge_graph as _save_kg, save_lesson

    try:
        save_lesson(
            deps.settings.SOURCES_CACHE_DIR,
            st.student_id or "", st.subject or "", topic, st.grade or "", lesson,
        )
    except Exception as exc:
        logger.warning("save_lesson: %s", exc)
    # Сохраняем граф знаний вместе с уроком — при reuse из кэша фронтенд получит nodes.
    # Для web-источников knowledge_graph может быть пустым — строим из sources.
    kg_data = st.knowledge_graph or {}
    if not kg_data.get("nodes") and st.sources:
        kg_data = _build_web_knowledge_graph(st.sources, topic)
    if kg_data.get("nodes"):
        try:
            _save_kg(
                deps.settings.SOURCES_CACHE_DIR,
                st.student_id or "", st.subject or "", topic, st.grade or "",
                {"nodes": kg_data["nodes"], "edges": kg_data.get("edges", [])},
            )
        except Exception as exc:
            logger.warning("save_knowledge_graph: %s", exc)


def _apply_cached_lesson(st: TutorState, deps: GraphDeps, cached, topic: str) -> Dict[str, Any]:
    """Показывает кэшированный урок: запись в состояние + события + вопрос «перейти к квизу?»."""
    from .lesson_cache import load_knowledge_graph as _load_kg

    st.set_lesson(cached)
    st.lesson_done = True
    # Восстанавливаем граф знаний из кэша — чтобы KnowledgeGraphPanel на фронтенде заполнился.
    kg_data = _load_kg(
        deps.settings.SOURCES_CACHE_DIR,
        st.student_id or "", st.subject or "", topic, st.grade or "",
    )
    if kg_data:
        st.knowledge_graph = kg_data
        logger.info("restore_kg from cache: %d nodes", len(kg_data.get("nodes", [])))
    else:
        logger.warning("restore_kg: нет сохранённого графа для темы «%s»", topic)
    _emit(deps, "tutor.lesson", **st.lesson_payload(topic))
    _emit(deps, "system",
          message="Показываю урок из прошлого занятия. Хочешь дополнить материал?",
          kind="lesson.cached")
    st.agent_question = "Готов(а) перейти к квизу? (да / нет)"
    _emit(deps, "intake.question", question=st.agent_question, missing_fields=["lesson_confirm"])
    return st.model_dump()


def _wiki_articles_for(st: TutorState, deps: GraphDeps) -> List[str]:
    """Тела wiki-статей тем графа/темы (баг #6): качественный контент добавляем к RAG-контексту.

    Wiki-статьи (накопленные между сессиями) часто содержат лучшее изложение, чем
    сырые чанки веб-скрапов. Их тела идут ПЕРВЫМИ в контекст урока — LLM получает
    объяснительный материал, а не только фрагменты источников.
    """
    try:
        from .wiki import KnowledgeWiki

        wiki = KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR,
                             student_id=getattr(st, "student_id", None) or "")
        subject = st.subject or "общая тема"
        bodies: List[str] = []
        titles: List[str] = []
        for n in (st.knowledge_graph or {}).get("nodes", []) or []:
            title = (n.get("title") or "").strip()
            if not title or n.get("type") in ("book", "page"):
                continue
            titles.append(title)
        # Тема сессии — приоритет, затем узлы графа
        if st.topic and st.topic not in titles:
            titles.insert(0, st.topic)
        for title in titles[:8]:
            art = wiki.get(subject, title)
            if art is not None and (art.body or "").strip():
                bodies.append(art.body.strip())
        return bodies
    except Exception as exc:
        logger.warning("_wiki_articles_for: %s", exc)
        return []# Тема «весь учебник»/не задана → нужен выбор темы из графа (topic gate).
# Конкретная тема (напр. «Дроби») → гейт пропускается (Уровень 1).
_ALL_TOPIC_MARKERS = {"all", "все", "всё", "вся", "весь", "весь учебник", "все темы"}


def _needs_topic_gate(st: TutorState) -> bool:
    topic = (st.topic or "").strip().lower()
    return (not topic) or topic in _ALL_TOPIC_MARKERS


def _auto_select_topic(st: TutorState) -> None:
    """Сопоставляет конкретную тему с узлом графа знаний; при совпадении — active_topic.

    Позволяет сразу готовить материал/квиз по теме, которую пользователь назвал
    при знакомстве, не переспрашивая «какую тему изучаем».
    """
    if not st.topic or not st.knowledge_graph:
        return
    from .knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph.from_dict(st.knowledge_graph or {})
    node_id = _match_topic(kg, st.topic)
    if node_id:
        st.active_topic = node_id


def _finalize_source(st: TutorState, *, web_sources: bool) -> None:
    """Уровень 1: после индексации решает, нужен ли выбор темы из графа.

    - Конкретная тема + веб-материалы (собраны по теме) → гейт не нужен, идём сразу.
    - Конкретная тема + загруженный учебник: если тема нашлась среди уроков графа →
      активная тема выбрана автоматически, гейт не нужен; если не нашлась → предлагаем
      выбрать урок из учебника (иначе RAG не найдёт релевантных фрагментов).
    - Тема «все»/не задана → гейт выбора темы.

    Источник стал готов → сессия больше не в терминальном состоянии. Критично для
    среды без веб-доступа: поиск упал (session_status="failed") → пользователь
    грузит учебник → здесь состояние сбрасывается, иначе route_tutor_agent уведёт
    в сводку вместо квиза/урока.
    """
    st.session_status = None
    st.quiz_complete = False
    if not _needs_topic_gate(st):
        if web_sources:
            st.awaiting_topic = False
            return
        _auto_select_topic(st)
        st.awaiting_topic = st.active_topic is None
    else:
        st.awaiting_topic = True


def _ontology_llm_call(deps: GraphDeps) -> Callable[[List[Dict[str, str]]], str]:
    """LLM-вызов для построения онтологии: инъекция (тесты) или реальный клиент (role=tutor)."""
    if callable(getattr(deps, "tutor_llm", None)):
        return deps.tutor_llm

    def _call(messages: List[Dict[str, str]]) -> str:
        from .llm_client import LLMClient

        return LLMClient(role="tutor").chat(messages, temperature=0.0, max_tokens=900).content or ""

    return _call


def _cheap_llm_call(deps: GraphDeps) -> Optional[Callable[[List[Dict[str, str]]], str]]:
    """LLM-вызов для cheap-задач (фильтрация тем, суммаризация).

    Использует cheap-модель (gemma-3-4b-it) для быстрого анализа.
    """
    if callable(getattr(deps, "cheap_llm", None)):
        return deps.cheap_llm

    def _call(messages: List[Dict[str, str]]) -> str:
        from .llm_client import LLMClient

        return LLMClient(role="cheap").chat(messages, temperature=0.3, max_tokens=500).content or ""

    return _call


def _schedule_wiki_extraction(st: TutorState, deps: GraphDeps, limit: int = 5) -> None:
    """Roadmap #2 (Wiki-LLM): индекс-время извлечение фактов в wiki-статьи.

    Фоновый поток (НЕ блокирует индексацию): до `limit` тем графа без конспекта
    получают статью из RAG-контекста через LLM. Best-effort — при недоступности
    LLM статьи остаются каркасами (lazy-enrich при первом ответе, enrich_body).
    """
    try:
        import threading

        t = threading.Thread(target=_wiki_extract_from_graph, args=(st, deps, limit), daemon=True)
        t.start()
    except Exception as exc:
        logger.warning("Wiki-извлечение не запланировано: %s", exc)


def _wiki_extract_from_graph(
    st: TutorState,
    deps: GraphDeps,
    limit: int,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> None:
    """Заполняет wiki-статьи тем графа конспектами из RAG-контекста (кап на batch)."""
    try:
        from .wiki import KnowledgeWiki

        kg_nodes = (st.knowledge_graph or {}).get("nodes", []) or []
        wiki = KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR,
                             student_id=getattr(st, "student_id", None) or "")
        subject = st.subject or "общая тема"
        done = 0
        for n in kg_nodes:
            if done >= limit:
                break
            title = (n.get("title") or "").strip()
            if not title or n.get("type") in ("book", "page"):
                continue
            art = wiki.get(subject, title)
            if art is not None and (art.body or "").strip():
                continue  # конспект уже есть
            chunks = _rag_chunks(deps.store, title, st, k=3)
            context = [_clean_text_lines(c.chunk.text) for c in chunks]
            if not context:
                continue
            if llm_call is None:
                from .llm_client import LLMClient

                client = LLMClient(role="tutor")
                llm_call = lambda msgs: client.chat(msgs, temperature=0.3, max_tokens=400).content or ""
            try:
                wiki.enrich_body(st, title, context, llm_call=llm_call)
                # источник информации (URL/учебник) из RAG-чанков
                src = next((c.chunk.source for c in chunks if c.chunk.source), "")
                if src:
                    wiki.set_source(st, title, src)
                done += 1
            except Exception as exc:
                logger.warning("Wiki-извлечение для «%s» не удалось: %s", title, exc)
        if done:
            _emit(deps, "wiki.updated", subjects=[subject])
    except Exception as exc:
        logger.warning("Wiki-извлечение из графа упало: %s", exc)

def _readable_title(url: str) -> str:
    """Извлекает читаемое название из URL или возвращает домен.

    Примеры:
        https://eduamti.ru/pluginfile.php/3109/mod_resource/content/1/... → "eduamti.ru"
        https://infourok.ru/magazin-materialov/konspekt-lekcii-... → "infourok.ru"
        "" → "Источник"
    """
    if not url:
        return "Источник"
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            return "Источник"
        # Отрезаем www.
        host = host.lstrip("www.")
        return host
    except Exception:
        return "Источник"


@dataclass
class GraphDeps:
    """Зависимости графа (инъекция для тестов)."""

    embedder: Embedder
    store: VectorStore
    tutor_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    cheap_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None  # cheap-модель для фильтрации
    eval_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    expert_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    judge_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    agent_llm: Optional[Callable[[List[Dict[str, str]], Optional[List[Dict[str, Any]]]], Any]] = None  # agent_loop (function calling)
    on_token: Optional[Callable[[str], None]] = None  # реальный стриминг токенов в браузер
    http: Optional[httpx.Client] = None
    settings: Any = None
    collection_name: str = "edututor"
    source_collector: Optional[Callable[..., Any]] = None  # override find_textbook
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None  # (event, data)
    step_logger: Any = None  # JsonlStepLogger: JSONL-трассировка запроса (request_id)
    student_store: Optional[Any] = None  # StudentStore (профили + KG ученика); fail-soft


def make_graph_deps(settings: Any = None) -> GraphDeps:
    """Стандартные зависимости (реальные embedder/хранилище, LLM-клиенты по умолчанию)."""
    s = settings or default_settings
    embedder = make_embedder(s)
    collection = make_collection_name(embedder)
    store = make_store(collection, embedder, persist_dir=Path(s.CHROMA_PERSIST_DIR), settings=s)
    if getattr(s, "HYBRID_RAG", True):
        from .knowledge import HybridVectorStore

        store = HybridVectorStore(store)
    from .student import StudentStore

    # Cheap-модель для фильтрации тем (gemma-3-4b-it)
    cheap_llm: Optional[Callable[[List[Dict[str, str]]], str]] = None
    try:
        from .llm_client import LLMClient

        cheap_llm = lambda msgs: LLMClient(role="cheap").chat(msgs, temperature=0.3, max_tokens=500).content or ""
    except Exception:
        logger.warning("cheap_llm не инициализирован")

    return GraphDeps(embedder=embedder, store=store, settings=s, collection_name=collection,
                     cheap_llm=cheap_llm,
                     student_store=StudentStore(root_dir=s.STUDENTS_DIR))


def _student_kg(deps: GraphDeps) -> Optional[Any]:
    """StudentStore для KG-обновлений (fail-soft: None при любой ошибке)."""
    try:
        if getattr(deps, "student_store", None) is not None:
            return deps.student_store
        if deps.settings is None:
            return None
        from .student import StudentStore

        return StudentStore(root_dir=deps.settings.STUDENTS_DIR)
    except Exception:
        return None


def _review_bank(deps: GraphDeps, st: TutorState):
    """ReviewBank для студента (fail-soft: None при выключенной фиче/ошибке)."""
    try:
        if not getattr(getattr(deps, "settings", None), "ENABLE_SPACED_REPETITION", True):
            return None
        if not getattr(st, "student_id", None):
            return None
        from .review import ReviewBank

        return ReviewBank(deps.settings.REVIEW_BANK_DIR, st.student_id,
                          max_cards=getattr(deps.settings, "REVIEW_BANK_MAX_CARDS", 200))
    except Exception as exc:
        logger.warning("ReviewBank unavailable: %s", exc)
        return None


def _rag_filters(state: TutorState) -> Dict[str, Any]:
    """Метаданные-фильтры RAG-поиска (subject/grade/topic/раздел активной темы/ученик)."""
    filters: Dict[str, Any] = {}
    if state.subject:
        filters["subject"] = state.subject
    if state.grade:
        filters["grade"] = state.grade
    # Фильтр по теме: чтобы источники разных тем не смешивались
    topic = getattr(state, "topic", None)
    if topic and topic != "all":
        filters["topic"] = topic
    # Материалы персональны: чанки другого ученика не смешиваются
    if getattr(state, "student_id", None):
        filters["student_id"] = state.student_id
    # Подготовка по теме: фильтр по разделу активного узла графа
    if state.active_topic:
        section = _active_topic_section(state)
        if section:
            filters["section_number"] = section
    logger.debug("RAG-фильтры: %s", filters)
    return filters


def _rag_chunks(store: VectorStore, query: str, state: TutorState, k: int = 3) -> List[Any]:
    """RAG-поиск с метаданными (нужно для section/параграфа в экспорте).

    Прогрессивное ослабление фильтров: строгий фильтр (класс/раздел) может обнулить
    результат при переиспользовании коллекции между сессиями/классами — тогда
    повторяем без класса, затем без раздела. Предмет не сбрасываем (корректность темы).

    Шумовые чанки (навигация сайтов, «-->», промо) отсекаются, чтобы урок/квиз
    не строились по мусору; если всё отсеяно — контент считается отсутствующим.
    """
    from .knowledge import SearchResult

    _t0 = time.monotonic()
    filters = _rag_filters(state)
    logger.debug("RAG-поиск: query=%r, k=%d, filters=%s", query, k, filters)
    results: List[SearchResult] = store.search(query, k=k, filters=filters or None)
    attempts = 1
    if not results:
        logger.info("RAG-поиск без результатов: query=%r, filters=%s", query, filters)
        # Обратная совместимость: старые чанки не имеют поля topic в метаданных.
        # Если topic-фильтр дал 0 результатов — пробуем ПЕРВЕЙДОМ без topic.
        active_filters = filters
        if "topic" in filters:
            no_topic = {k: v for k, v in filters.items() if k != "topic"}
            if no_topic:
                results = store.search(query, k=k, filters=no_topic)
                active_filters = no_topic
            else:
                results = store.search(query, k=k, filters=None)
                active_filters = {}
            attempts += 1
            if results:
                logger.info("RAG-поиск succeed после снятия topic-фильтра (обратная совместимость): найдено %d", len(results))
    if not results:
        for drop in ("grade", "section_number"):
            relaxed = {kk: vv for kk, vv in active_filters.items() if kk != drop}
            results = store.search(query, k=k, filters=relaxed or None)
            attempts += 1
            if results:
                logger.info("RAG-поиск succeed после снятия фильтра %s: найдено %d", drop, len(results))
                break
    _t_search = time.monotonic() - _t0
    meaningful = [r for r in results if _chunk_has_meaningful_content(r.chunk.text)]
    _t_total = time.monotonic() - _t0
    logger.info(
        "RAG-поиск: query=%r topic=%r attempts=%d raw=%d meaningful=%d search=%.3fs total=%.3fs",
        query, getattr(state, "topic", None), attempts, len(results), len(meaningful), _t_search, _t_total,
    )
    if len(meaningful) < len(results):
        logger.info("RAG-поиск: отсеяно шумных чанков %d из %d", len(results) - len(meaningful), len(results))
    if meaningful:
        topics_seen = set()
        for r in meaningful[:5]:
            t = getattr(r.chunk, "topic", None) or (r.chunk.metadata().get("topic") if isinstance(r.chunk.metadata(), dict) else None)
            topics_seen.add(t or "(none)")
        logger.debug("RAG-поиск: найдено %d осмысленных чанков, темы: %s", len(meaningful), topics_seen)
    return meaningful


def _chunk_has_meaningful_content(text: str) -> bool:
    """Чанк — учебный контент, а не навигация/заголовки портала.

    Заголовки-агрегаторов («Литературная гостиная …», «Тест …») длиной 30-50
    символов проходят старый фильтр, но урок из них не собрать. Требуем прозу:
      - длинное предложение (≥80 символов), либо
      - несколько строк ≥40 символов, хотя бы с точкой/вопросом в конце, либо
      - несколько коротких предложений с пунктуацией (абзац-конспект).
    """
    from .knowledge import _is_web_noise

    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    meaningful = [ln for ln in lines if not _is_web_noise(ln)]
    if not meaningful:
        return False
    # Длинное предложение — точно контент
    if any(len(ln) >= 80 for ln in meaningful):
        return True
    # Прозные предложения заканчиваются точкой/вопросом (а не заголовки-списки)
    def _is_sentence(ln: str) -> bool:
        return bool(ln) and ln.rstrip("…\"»")[-1:] in (".", "!", "?")
    long_enough = [ln for ln in meaningful if len(ln) >= 40]
    if len(long_enough) >= 2 and any(_is_sentence(ln) for ln in long_enough):
        return True
    # Короткий абзац: несколько предложений с пунктуацией
    small = [ln for ln in meaningful if len(ln) >= 25 and _is_sentence(ln)]
    return len(small) >= 2


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


_MODE_LABELS = {"lesson": "урок", "quiz": "квиз", "explain": "объяснение", "deep_dive": "глубокий разбор"}


def _intent_message(st: TutorState) -> str:
    """Уровень 3: короткое подтверждение намерения перед долгой операцией (поиск/индексация).

    Подтверждает понимание, НЕ обещая готовый урок до того, как материалы найдены
    (иначе при провале поиска читается «Готовлю урок → не найдены»).
    """
    mode = _MODE_LABELS.get(st.mode or "", "занятие")
    topic = st.topic if st.topic and st.topic != "all" else (st.subject or "тему")
    return f"Принято: {mode} по теме «{topic}». Начинаю работу…"


# ----------------------------------------------------------------------
# Узлы
# ----------------------------------------------------------------------
def intake_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)

    # Инициализация адаптивной модели ученика (LinUCB) — если включена настройкой
    if st.bandit is None and getattr(deps.settings, "ADAPTIVE_BANDIT", True):
        st.bandit = adaptive.make_bandit()

    # Применяем ответ на текущий вопрос чек-листа.
    # Уровень 2 (5.4): свободный ответ может заполнить СРАЗУ несколько полей
    #    (например: «я в 7 классе, алгебра, дроби, учебника нет, хочу квиз» →
    #    extract_intake_fields → learner_type+grade+subject+topic+has_textbook+mode).
    # ВАЖНО: если intake уже завершён (missing пусто) — ответ НЕ трогаем: он
    # принадлежит нижестоящему узлу (подтверждение урока, ответ на вопрос квиза и т.п.).
    if st.pending_answer is not None and (st.intake_field is not None or compute_missing(st)):
        answer_text = st.pending_answer
        extracted = extract_intake_fields(answer_text)
        applied_any = False
        for field_name, value in extracted.items():
            if value is not None and field_name in compute_missing(st):
                st = apply_answer(st, field_name, value)
                # applied_any — только если поле реально закрылось (значение принято)
                if field_name not in compute_missing(st):
                    applied_any = True
        if not applied_any and compute_missing(st):
            if st.intake_field is None:
                for field_name in CHECKLIST_ORDER:
                    if field_name in compute_missing(st):
                        st.intake_field = field_name
                        break
            if st.intake_field:
                st = apply_answer(st, st.intake_field, answer_text)
                st.intake_field = None
        st.pending_answer = None
        # Ответ обработан: поле сбрасываем, иначе after_intake примет его за незавершённый
        # intake и остановит граф на чек-листе (validate_intake переустановит поле при «ask»)
        st.intake_field = None
        # Ученик ответил текстом — карточка больше не нужна (переходим к Q&A)
        st.agent_card = None

    # Быстрое знакомство: на старте (ещё нет прогресса) показываем карточку-форму,
    # а не пошаговые вопросы. Ученик заполняет поля сразу (POST /intake/card).
    st, card_started = maybe_start_card(st)
    if card_started:
        _emit(deps, "intake.card", card=st.agent_card, question=st.agent_question)
        return st.model_dump()

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


def agent_intake_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Уровень 4 (спека 2.3, 5.4): intake ведёт агент через function calling.

    Модель сама выбирает действие (инструменты интервью), получает результат и решает,
    продолжать интервью или завершить. При недоступности агентной LLM (например, в тестах
    задан только Callable tutor_llm) — детерминированный intake_node (фолбэк).
    """
    from .agent_loop import agent_available, run_intake_agent

    if not agent_available(deps):
        return intake_node(state, deps)

    st = state.model_copy(deep=True)

    # Быстрое знакомство: на старте (нет ответа, ещё нет прогресса) показываем
    # карточку-форму (без ожидания, вызовет ли модель build_intake_card).
    # Если ученик уже ответил текстом — карточку не показываем, обрабатываем ответ.
    if st.pending_answer is None:
        st, card_started = maybe_start_card(st)
        if card_started:
            _emit(deps, "intake.card", card=st.agent_card, question=st.agent_question)
            return st.model_dump()

    st_new, proceed = run_intake_agent(st, deps)
    if proceed:
        # grade_curriculum: сверка темы с ФГОС (В-8) — как в детерминированном intake_node
        if st_new.subject and st_new.topic and not st_new.curriculum:
            cur = grade_curriculum(st_new.subject, st_new.grade, st_new.topic,
                                   ref_dir=deps.settings.FGOS_REFERENCE_DIR)
            if cur.fgos_code:
                st_new.curriculum = cur.fgos_code
            else:
                st_new.curriculum = "unverified"
                if not st_new.agent_message:
                    st_new.agent_message = cur.warning
        return st_new.model_dump()
    if st_new.agent_card:
        # Модель сама собрала карточку (build_intake_card) — публикуем форму
        _emit(deps, "intake.card", card=st_new.agent_card, question=st_new.agent_question)
    else:
        # Агент задал вопрос — публикуем как в детерминированном intake_node
        _emit(deps, "intake.question", question=st_new.agent_question,
              missing_fields=st_new.missing_fields or compute_missing(st_new))
    return st_new.model_dump()


def route_after_agent_intake(state: TutorState) -> str:
    """После агентного intake: задан вопрос → ждём ответ; иначе — на источник."""
    if compute_missing(state) and not getattr(state, "session_status", None) == "failed":
        return END
    return NODE_SOURCE_ENTRY


def deterministic_tutor_step(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Детерминированный цикл квиза (фолбэк agent_tutor_node при недоступности агента).

    Воспроизводит поведение route_tutor: первый вопрос / оценка+следующий. Сводка —
    отдельным узлом NODE_SUMMARY через route_after_agent_tutor.
    """
    st = state.model_copy(deep=True)
    if st.quiz_complete or st.session_status in ("completed", "failed"):
        return st.model_dump()
    if st.current_question is None:
        return generate_question_node(st, deps)
    if st.pending_answer is not None:
        st = TutorState.model_validate(evaluate_answer_node(st, deps))
        if st.quiz_complete or st.session_status == "completed":
            return st.model_dump()
        return generate_question_node(st, deps)
    return generate_question_node(st, deps)


def agent_tutor_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Уровень 5 (спека 7.3.1): квиз ведёт агент — модель сама выбирает следующее действие
    через function calling (evaluate_answer / generate_quiz / explain_error / deep_dive /
    finish_session). При недоступности агентной LLM — детерминированный цикл квиза.
    
    Авто-урок: если mode="lesson" и урок ещё не показан, генерируем его автоматически
    перед запуском агента (аналогично content_node, но без стриминга on_token).
    """
    from .agent_loop import agent_available, run_tutor_agent

    if not agent_available(deps):
        return deterministic_tutor_step(state, deps)

    st = state.model_copy(deep=True)
    
    # Автоматически генерируем урок, если mode="lesson" и он ещё не был показан
    if (st.mode == "lesson" and not st.lesson_done and st.lesson_text is None):
        topic = st.topic or st.subject or "общая тема"
        if st.active_topic:
            from .knowledge_graph import KnowledgeGraph
            kg = KnowledgeGraph.from_dict(st.knowledge_graph or {})
            for n in kg.to_dict()["nodes"]:
                if n.get("id") == st.active_topic:
                    title = n.get("title", "")
                    if title:
                        topic = title
        # Кэш урока (3.1/7.2): повторное прохождение темы — урок из прошлого раза.
        cached_lesson = _load_cached_lesson(st, deps, topic)
        if cached_lesson is not None:
            logger.info("agent_tutor_node: кэшированный урок по теме «%s» — без генерации", topic)
            st.set_lesson(cached_lesson)
            st.lesson_done = True
            # Восстанавливаем граф знаний из кэша для reuse_gate пути.
            from .lesson_cache import load_knowledge_graph as _load_kg
            kg_data = _load_kg(
                deps.settings.SOURCES_CACHE_DIR,
                st.student_id or "", st.subject or "", topic, st.grade or "",
            )
            if kg_data:
                st.knowledge_graph = kg_data
                logger.info("agent_tutor_node: восстановлен graph из cache %d nodes", len(kg_data.get("nodes", [])))
            else:
                logger.warning("agent_tutor_node: нет сохранённого графа для «%s»", topic)
            _emit(deps, "tutor.lesson", **st.lesson_payload(topic))
            _emit(deps, "system",
                  message="Показываю урок из прошлого занятия. Хочешь дополнить материал?",
                  kind="lesson.cached")
            return st.model_dump()
        _emit(deps, "source.progress", stage="content", url="", status="generating",
              message=f"Ищу материалы по теме «{topic}»…")
        chunks = _rag_chunks(deps.store, topic, st, k=5)
        # RAG-first гейт: без контекста урок не выдумываем — сообщаем и ждём источник
        if not chunks:
            st.agent_message = (
                f"По теме «{topic}» пока нет материала в загруженных источниках. "
                "Загрузите учебник (PDF/DOCX) или нажмите «Найти учебник» — и я подготовлю урок."
            )
            # agent_question обязателен: иначе CLI/веб зациклится на «внутреннем шаге»
            st.agent_question = st.agent_message
            _emit(deps, "source.progress", stage="content", url="", status="empty",
                  message=st.agent_message)
            _emit(deps, "system", message=st.agent_message, kind="content.empty")
            return st.model_dump()
        context = [_clean_text_lines(c.chunk.text) for c in chunks]
        # Метаданные источника параллельно context (для честного groundedness):
        # wiki-статьи не имеют чанка-источника → None, чанки — их meta.
        sources: List[Optional[Dict[str, Any]]] = [c.chunk.metadata() for c in chunks]
        # Баг #6: wiki-статьи дополняют RAG-контекст урока (качественный контент первым).
        wiki_bodies = _wiki_articles_for(st, deps)
        if wiki_bodies:
            context = wiki_bodies + context
            sources = [None] * len(wiki_bodies) + sources
        _emit(deps, "source.progress", stage="content", url="", status="generating",
              message=f"Генерирую урок по теме «{topic}» ({len(context)} фрагментов)…")
        # Стриминг токенов урока: on_token → WS event "token" → фронтенд pushToken → пользователь видит прогресс
        original_on_token = deps.on_token  # реальный стриминг токенов в браузер (stream=True)
        # Встраиваем промежуточные события прогресса в on_token — каждые N токенов эмиссим milestone
        token_count = [0]  # mutable counter shared across closures
        def counting_on_token(token: str) -> None:
            if original_on_token:
                original_on_token(token)
            token_count[0] += 1
            count = token_count[0]
            if count == 50:
                _emit(deps, "source.progress", stage="content", url="", status="generating",
                      message="Обрабатываю материалы…")
            elif count == 150:
                _emit(deps, "source.progress", stage="content", url="", status="generating",
                      message="Формирую структуру урока…")
            elif count == 300:
                _emit(deps, "source.progress", stage="content", url="", status="generating",
                      message="Сверяю с источниками…")
        try:
            # Таймаут генерации: защита от зависания LLM-вызова (P0.2)
            from .config import settings as graph_settings
            _timeout_sec = getattr(graph_settings, "LESSON_GENERATION_TIMEOUT_SEC", 120)

            def _do_generate():
                return tutor_mod.generate_lesson(
                    topic, context, st, llm_call=deps.tutor_llm, on_token=counting_on_token, sources=sources
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
                lesson = _pool.submit(_do_generate).result(timeout=_timeout_sec)

            # Quality gate удалён: новый стриминговый pipeline генерирует чистый текст,
            # fallback на контекст встроен в generate_lesson — мусор не проходит.
            st.set_lesson(lesson)
            st.lesson_sources = _extract_lesson_sources(sources, st.sources)
            st.lesson_done = True
            _save_lesson_to_cache(st, deps, lesson, topic)
            _emit(deps, "tutor.lesson", **st.lesson_payload(topic))
            _emit(deps, "source.progress", stage="content", url="", status="done",
                  message="Урок готов.")

        except concurrent.futures.TimeoutError:
            logger.error("agent_tutor_node: таймаут генерации урока по «%s» (%ss)", topic, _timeout_sec)
            _emit(deps, "source.progress", stage="content", url="", status="error",
                  message="Не удалось сгенерировать урок: превышено время ожидания.")
            st.agent_message = (
                f"Урок по теме «{topic}» не успел сформироваться — попробуйте ещё раз или выберите другую тему."
            )
            st.agent_question = st.agent_message

        except Exception as exc:
            logger.exception("agent_tutor_node: ошибка генерации урока по «%s»: %s", topic, exc)
            _emit(deps, "source.progress", stage="content", url="", status="error",
                  message="Не удалось сгенерировать урок: ошибка сервера.")
            st.agent_message = (
                f"Произошла ошибка при подготовке урока по теме «{topic}». "
                "Попробуйте перезагрузить страницу или выбрать другую тему."
            )
            st.agent_question = st.agent_message

    # Урок только что показан — ждём ответ ученика, не запускаем агент.
    # Иначе ReAct-цикл вернёт сырой текст или вызовет generate_quiz до
    # того, как ученик прочитает урок. lesson_confirmed уже истинен, когда
    # ученик ответил «да» (content_node) — тогда пропускаем гейт и идём в квиз.
    if st.lesson_done and st.current_question is None and st.answered_count == 0 \
            and not st.pending_answer and not st.lesson_confirmed:
        st.agent_question = "Готов(а) перейти к квизу? (да / нет)"
        _emit(deps, "system",
              message="Урок по теме готов. Можно задать вопрос или перейти к квизу.",
              kind="lesson.ready")
        return st.model_dump()

    # Свободный вопрос ПОСЛЕ урока (квиз ещё не начат): отвечаем по RAG, квиз не запускаем.
    # Слабая модель на вопрос ученика иначе «пересказывает урок» или сразу зовёт generate_quiz.
    from .agent_loop import _answer_free_question, _is_not_ready, _is_ready_to_quiz, _looks_like_agent_command

    prev_qid = st.current_question.question_id if st.current_question else None
    prev_lesson = st.lesson_text

    if st.lesson_done and st.current_question is None and st.answered_count == 0 \
            and st.pending_answer:
        user_text = st.pending_answer
        st.pending_answer = None

        # Команда агента (глубокий разбор, показать урок, объяснить тему) → ReAct-цикл
        if _looks_like_agent_command(user_text):
            _emit(deps, "source.progress", stage="tutor", url="", status="generating",
                  message=f"Обрабатываю запрос: «{user_text}»…")
            st, final_text = run_tutor_agent(st, deps)
            # Обработка результатов: урок/разбор сгенерирован → обновляем lesson_text
            if st.lesson_text and st.lesson_text != prev_lesson:
                _emit(deps, "tutor.lesson", **st.lesson_payload(st.topic or st.subject or "тема"))
                _emit(deps, "system", message="Готово. Можно задать вопрос или перейти к квизу.",
                      kind="lesson.ready")
            # Если агент сгенерировал вопрос → показываем квиз
            if st.current_question and st.current_question.question_id != prev_qid:
                card = st.current_question
                _emit(deps, "quiz.card", question_id=card.question_id, question=card.question,
                      options=card.options, answer_type=card.answer_type, difficulty=card.difficulty,
                      topic=card.topic, num_questions=st.num_questions,
                      question_num=st.answered_count + 1)
            return st.model_dump()

        if not _is_ready_to_quiz(user_text):
            if _is_not_ready(user_text):
                st.agent_message = (
                    "Хорошо. Изучите урок ещё раз — если что-то непонятно, спросите, "
                    "и я объясню. Когда будете готовы — напишите «да»."
                )
            else:
                st.agent_message = _answer_free_question(st, deps, user_text)
            _emit(deps, "system", message=st.agent_message, kind="agent.message")
            return st.model_dump()

    # Готов к квизу: запускаем генерацию первого вопроса напрямую (без агента).
    # Агент не знает как обработать "да/готов/начинаем" — он ждёт ответ на активный вопрос.
    if st.lesson_done and st.current_question is None and st.answered_count == 0 \
            and st.pending_answer and _is_ready_to_quiz(st.pending_answer):
        st.pending_answer = None
        st.lesson_confirmed = True
        _emit(deps, "system", message="Отлично! Начинаем квиз.", kind="lesson.done")
        _emit(deps, "source.progress", stage="quiz", url="", status="generating",
              message=f"Генерирую первый вопрос по теме «{st.topic or st.subject or 'тема'}»…")
        st = TutorState.model_validate(generate_question_node(st, deps))
        if st.current_question:
            card = st.current_question
            _emit(deps, "quiz.card", question_id=card.question_id, question=card.question,
                  options=card.options, answer_type=card.answer_type, difficulty=card.difficulty,
                  topic=card.topic, num_questions=st.num_questions,
                  question_num=st.answered_count + 1)
        return st.model_dump()

    # Быстрая оценка ответа (если есть активный вопрос и ответ ученика):
    # для закрытых вопросов — мгновенно (reference matching), для открытых — LLM.
    # Фидбек "Верно/Неверно" эмитится сразу, генерация следующего вопроса — после.
    if st.mode == "quiz" and st.current_question and st.pending_answer:
        from .evaluation import evaluate_and_record

        def _local_emit(event: str, **data: Any) -> None:
            _emit(deps, event, **data)

        card = st.current_question
        answer = st.pending_answer
        st.pending_answer = None
        _emit(deps, "source.progress", stage="tutor", url="", status="evaluating",
              message="Оцениваю ответ…")
        st, message, _j, _e = evaluate_and_record(st, deps, card, answer, emit=_local_emit)
        # Эмитим фидбек сразу (не ждём генерации следующего вопроса)
        _emit(deps, "tutor.explanation" if not getattr(st, "quiz_complete", False) else "system",
              message=message,
              correct_count=st.correct_count,
              answered_count=st.answered_count)
        # Генерируем следующий вопрос (если квиз не завершён)
        if not st.quiz_complete and st.answered_count < (st.num_questions or 10):
            _emit(deps, "source.progress", stage="quiz", url="", status="generating",
                  message=f"Генерирую следующий вопрос…")
            st = TutorState.model_validate(generate_question_node(st, deps))
            if st.current_question:
                card = st.current_question
                _emit(deps, "quiz.card", question_id=card.question_id, question=card.question,
                      options=card.options, answer_type=card.answer_type, difficulty=card.difficulty,
                      topic=card.topic, num_questions=st.num_questions,
                      question_num=st.answered_count + 1)
        return st.model_dump()

    _emit(deps, "source.progress", stage="tutor", url="", status="generating",
          message=f"Готовлю задание по теме «{st.topic or st.subject or 'тема'}»…")
    st, final_text = run_tutor_agent(st, deps)

    # СТРАХОВКА (баг #3): ответ оценён, но модель не вызвала generate_quiz —
    # новый вопрос не появился → генерируем детерминированно, чтобы квиз не завис.
    if (st.current_question is None or st.current_question.question_id == prev_qid) \
            and not st.quiz_complete and st.answered_count < (st.num_questions or 10):
        logger.warning("agent_tutor_node: модель не сгенерировала следующий вопрос — детерминированный fallback")
        st = TutorState.model_validate(generate_question_node(st, deps))

    # Публикуем события для фронтенда по изменениям состояния
    if st.current_question and st.current_question.question_id != prev_qid:
        card = st.current_question
        _emit(deps, "quiz.card", question_id=card.question_id, question=card.question,
              options=card.options, answer_type=card.answer_type, difficulty=card.difficulty,
              topic=card.topic, num_questions=st.num_questions,
              question_num=st.answered_count + 1)
    if st.lesson_text and st.lesson_text != prev_lesson:
        _emit(deps, "tutor.lesson", **st.lesson_payload(st.active_topic or st.topic or "тема"))
    if final_text and not st.current_question and not st.quiz_complete:
        _emit(deps, "system", message=final_text, kind="agent.message")
    return st.model_dump()


def route_after_agent_tutor(state: TutorState) -> str:
    """После агентного хода квиза: завершён → сводка; иначе ждём ответ (END)."""
    if state.quiz_complete or state.session_status == "completed":
        return NODE_SUMMARY
    return END


def after_intake(state: TutorState) -> str:
    # Ждём, пока ученик заполнит карточку знакомства (иначе граф уйдёт на источник
    # с незаполненным чек-листом)
    if state.intake_field or state.agent_card:
        return END
    return NODE_SOURCE_ENTRY


def source_entry(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    if st.sources or st.collection_id:
        st.source_status = "ready"
        return st.model_dump()
    return {}


def route_source(state: TutorState) -> str:
    if state.source_status == "failed":
        return NODE_SOURCE_FAILED
    if state.sources or state.collection_id or state.source_status == "ready":
        return NODE_TOPIC_GATE
    if state.textbook_file:
        return NODE_PROCESS_DOCUMENT
    if state.has_textbook is True:
        # «да, есть учебник», но файл ещё не загружен — ждём загрузку, а не веб-поиск
        return NODE_WAIT_FOR_UPLOAD
    # Нет файла и нет учебника: сначала проверяем, есть ли уже разобранные материалы
    # по теме (переиспользование), и только при их отсутствии/отказе — веб-поиск.
    return NODE_REUSE_GATE


# Ответы на вопрос «использовать существующие материалы или искать другие?»
def _reuse_decision(answer: Optional[str]) -> str:
    """Решение по ответу ученика: reuse | search.

    «да»-семейство → reuse; «нет»-семейство («искать», «найти другие») → search;
    неясный ответ → reuse (безопасно: не гоняем веб-поиск без явного запроса).
    """
    a = (answer or "").strip().lower()
    if not a:
        return "reuse"
    if a.startswith("да") or a.startswith("yes") or a.startswith("у") or "использ" in a:
        return "reuse"
    if (a.startswith("нет") or a.startswith("no")
            or "искать" in a or "найти" in a or "другие" in a
            or "не использ" in a):
        return "search"
    return "reuse"


def _reindex_cached_sources(st: TutorState, deps: GraphDeps, sources: List[Dict[str, Any]]) -> int:
    """Переиндексация кэшированных веб-материалов в векторный стор.

    Читает тексты из по-URL кэша (SOURCES_CACHE_DIR), режет на чанки текущим
    кодом (слайд-шоу отсекаются, student_id проставляется) и добавляет в стор.
    Возвращает число проиндексированных источников.
    """
    from . import source_finder as _sf

    added = 0
    chunks: List[Any] = []
    for s in sources:
        url = s.get("url", "")
        if not url:
            continue
        try:
            text = _sf._cache_read(url, deps.settings.SOURCES_CACHE_DIR)
        except Exception:
            text = None
        if not text:
            continue
        sc = _make_chunks(text, source=url, subject=st.subject, grade=st.grade,
                          topic=st.topic, student_id=getattr(st, "student_id", None) or None)
        if not sc:
            continue
        chunks.extend(sc)
        added += 1
    if chunks:
        deps.store.add(chunks)
        logger.info("reuse_gate: переиндексировано %d источников (%d чанков)", added, len(chunks))
    return added


def _student_has_chunks(st: TutorState, deps: GraphDeps) -> bool:
    """Есть ли в хранилище чанки ученика по теме (для переиспользования материалов)."""
    query = (st.topic or st.subject or "").strip()
    if not query:
        return False
    filters: Dict[str, Any] = {}
    if st.subject:
        filters["subject"] = st.subject
    if getattr(st, "student_id", None):
        filters["student_id"] = st.student_id
    # Фильтр по теме: чтобы чанки других тем (даже того же предмета) не считались подходящими.
    topic = getattr(st, "topic", None)
    if topic and topic != "all":
        filters["topic"] = topic
    try:
        return bool(deps.store.search(query, k=1, filters=filters or None))
    except Exception:
        return False


def _topic_studied_before(st: TutorState, deps: GraphDeps, topic: str) -> bool:
    """Тема пройдена ранее: есть в Student KG со статусом in_progress/mastered.

    По запросу «если тема пройдена ранее, считаем что материалы есть» — Student KG
    становится источником правды для reuse-гейта (а не только RAG-чанки по предмету).
    """
    # Если пользователь явно запросил поиск свежих источников — не считаем тему
    # «пройденной», чтобы не блокировать обновление материалов кэшированным уроком.
    if getattr(st, "force_source_refresh", False):
        return False
    if not topic or not getattr(st, "student_id", None):
        return False
    try:
        store = _student_kg(deps)
        if store is None:
            return False
        kg = store.get_knowledge_graph(st.student_id)
        if kg is None:
            return False
        low = topic.strip().lower()
        for ts in kg.topics.values():
            if ts.status not in ("in_progress", "mastered"):
                continue
            title = (ts.title or "").strip().lower()
            tid = (ts.topic_id or "").strip().lower()
            if low == title or low == tid or low in title or low in tid:
                return True
    except Exception:
        return False
    return False


def reuse_materials_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Гейт переиспользования: если по теме уже есть разобранные материалы ученика —
    спрашиваем, использовать ли их (поиск других — только по явному решению).

    Улучшение (6.3): на первом входе проверяет кэш материалов по (subject, topic, grade).
    Если в кэше есть данные — сразу устанавливает st.sources, st.collection_id,
    st.source_status = "ready" и пропускает NODE_FIND_TEXTBOOK.

    Также проверяет RAG-хранилище на наличие чанков по теме.
    """
    st = state.model_copy(deep=True)

    # Решение ученика уже пришло
    if st.reuse_pending and st.pending_answer is not None:
        answer_text = st.pending_answer
        st.pending_answer = None
        st.reuse_pending = False
        st.agent_question = None
        st.agent_options = None
        low = answer_text.strip().lower()
        if "с нуля" in low or "заново" in low:
            # «Начать с нуля»: урок из кэша убираем, ищем свежие материалы
            st.clear_lesson()
            st.force_source_refresh = True
            st.source_note = "Начинаем с нуля — ищу новые материалы по теме."
            st.agent_message = st.source_note
        elif "дополнить" in low or "искать" in low or "найти" in low:
            # «Дополнить материал»: старый урок остаётся, ищем новые источники
            st.force_source_refresh = True
            st.source_note = "Ищу дополнительные материалы по теме."
            st.agent_message = st.source_note
        elif "квиз" in low or "перейти" in low or "дальше" in low:
            # «Перейти к квизу»: кэшированный урок показан, существующие материалы готовы
            st.lesson_confirmed = True
            st.source_status = "ready"
            st.collection_id = getattr(deps.store, "collection_name", None) or "existing"
            st.sources = [{"type": "existing", "note": "ранее разобранные материалы"}]
            st.source_note = "Отлично! Переходим к квизу по теме."
            _finalize_source(st, web_sources=True)
            _emit(deps, "system", message="Отлично! Переходим к квизу.", kind="lesson.done")
        elif _reuse_decision(answer_text) == "search":
            st.source_note = "Ищу другие материалы по теме."
        else:
            # используем существующие материалы (без нового поиска)
            st.source_status = "ready"
            st.collection_id = getattr(deps.store, "collection_name", None) or "existing"
            st.sources = [{"type": "existing", "note": "ранее разобранные материалы"}]
            st.source_note = "Использую уже разобранные материалы по теме — поиск не нужен."
            _finalize_source(st, web_sources=True)
            _emit(deps, "system", message=st.source_note, kind="source.reused")
        return st.model_dump()

    # Первый вход:
    
    # A) Проверяем кэш материалов по (subject::topic::grade). Ключ включает
    # student_id — материалы учеников изолированы (из одного предмета/темы у
    # разных детей могут быть разные учебники и источники).
    # force_source_refresh (явный клик «Найти учебник») обходит кэш — мусорные
    # или устаревшие материалы не подставляются повторно.
    _sid = st.student_id or "anon"
    cache_key = f"{st.subject}::{st.topic or ''}::{st.grade or ''}"
    _cache_key_student = f"{_sid}::{cache_key}"
    from . import source_finder as _sf
    cached = None if st.force_source_refresh else _sf._get_cached_materials(
        _cache_key_student, cache_dir=deps.settings.SOURCES_CACHE_DIR)
    if cached is not None:
        cached_sources = cached.get("sources", [])
        cached_collection = cached.get("collection_id")
        # Политика источников: кэш, нарушающий белый список, не подставляем
        _wl_ok = (st.allow_any_sources
                  or all(_sf._domain_allowed(x.get("url", ""), st.source_whitelist) for x in cached_sources))
        if _wl_ok and cached_sources:
            # Кэш материалов + векторный стор должны быть согласованы. Если в сторе
            # нет чанков ученика (кэш создан старой версией / коллекция пересоздана),
            # переиндексируем тексты из по-URL кэша — иначе RAG-поиск вернёт пусто.
            if not _student_has_chunks(st, deps):
                logger.info("reuse_gate: чанки ученика не найдены в сторе — переиндексация из кэша %s",
                            cache_key)
                _reindex_cached_sources(st, deps, cached_sources)
            st.sources = cached_sources
            st.source_status = "ready"
            st.source_note = f"Кэшированные материалы: {len(cached_sources)} источников"
            if cached_collection:
                st.collection_id = cached_collection
            _finalize_source(st, web_sources=True)
            _emit(deps, "system", message=st.source_note, kind="source.cached")
            logger.info("reuse_gate: использован кэш материалов %s", cache_key)
            return st.model_dump()
    
    # B) Проверяем, что по теме есть реально разобранные материалы. По запросу:
    #    «если тема пройдена ранее, считаем что материалы есть» — источник правды —
    #    Student KG (статус in_progress/mastered) + строгие чанки с topic==теме.
    #    Ослабленный RAG-поиск (снятие topic-фильтра) здесь НЕ используется: чанки
    #    других тем предмета не являются материалами по запрошенной теме.
    if st.reuse_pending:
        return st.model_dump()  # вопрос задан, ждём ответ (route_reuse → END)
    if not st.allow_any_sources:
        return st.model_dump()
    query = st.topic or st.subject or ""
    if query:
        topic = st.topic or st.subject or "этой теме"
        # Материалы по теме (строгий критерий, без ослабления фильтров):
        #   - чанки с topic == теме (точечная разметка источников по теме), ИЛИ
        #   - тема пройдена ранее (Student KG: in_progress/mastered).
        # Ослабленный RAG-поиск (снятие topic-фильтра) НЕ используется: чанки других
        # тем или старые чанки без topic не являются доказательством «материалы есть».
        try:
            filters = _rag_filters(st)
            existing = deps.store.search(query, k=3, filters=filters or None) if "topic" in filters else []
        except Exception:
            existing = []
        studied = _topic_studied_before(st, deps, st.topic or "")
        if existing or studied:
            st.reuse_pending = True
            # 3.2: при повторном прохождении темы в режиме «урок» — сразу показываем
            # кэшированный урок из прошлого раза, а не спрашиваем «использовать да/нет».
            cached_lesson = _load_cached_lesson(st, deps, topic)
            if cached_lesson is not None:
                st.set_lesson(cached_lesson)
                st.lesson_done = True
                # Восстанавливаем граф знаний из кэша для reuse_gate пути.
                from .lesson_cache import load_knowledge_graph as _load_kg
                kg_data = _load_kg(
                    deps.settings.SOURCES_CACHE_DIR,
                    st.student_id or "", st.subject or "", topic, st.grade or "",
                )
                if kg_data:
                    st.knowledge_graph = kg_data
                    logger.info("reuse_gate: восстановлен graph из cache %d nodes", len(kg_data.get("nodes", [])))
                else:
                    logger.warning("reuse_gate: нет сохранённого графа для «%s»", topic)
                _emit(deps, "tutor.lesson", **st.lesson_payload(topic))
                st.agent_question = (
                    "Вот урок из прошлого раза. Хочешь дополнить материал из новых источников?"
                )
                st.agent_options = ["Перейти к квизу", "Дополнить материал", "Начать с нуля"]
                _emit(deps, "system", message="Показываю урок из прошлого занятия.",
                      kind="lesson.cached")
                _emit(deps, "intake.question", question=st.agent_question,
                      missing_fields=["reuse"], options=st.agent_options)
                return st.model_dump()
            st.agent_question = (
                f"По теме «{topic}» у тебя уже есть разобранные материалы. "
                "Использовать их (да) или найти другие (нет)?"
            )
            st.agent_options = ["Да, использовать", "Нет, найти другие"]
            _emit(deps, "intake.question", question=st.agent_question,
                  missing_fields=["reuse"], options=st.agent_options)
            return st.model_dump()
    return st.model_dump()


def route_reuse(state: TutorState) -> str:
    """После гейта переиспользования: ждём ответа; готово → источник/тема; иначе — поиск."""
    if state.reuse_pending:
        return END
    if state.source_status == "ready" or state.sources or state.collection_id:
        return NODE_TOPIC_GATE
    # «Начать с нуля» / «Дополнить»: force_source_refresh=True — останавливаем invoke,
    # чтобы пользователь увидел подтверждение. Следующий invoke запустит поиск с флагом.
    if state.force_source_refresh:
        return END
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
            # Единый ключ темы: st.topic = название узла графа (как в select_topic API),
            # иначе knowledge_map/wiki не совпадут с title узла и граф не окрасится мастерством.
            st.topic = title
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
    """После гейта: выбрана тема → контент по режиму (урок/объяснение/разбор) или квиз; иначе ждём (END).

    Уровень 2: если режим lesson/explain/deep_dive и тема не выбрана — пропускаем гейт,
    используя subject/topic/предмет как fallback для RAG-контекста.
    """
    wants_auto_content = state.mode in ("lesson", "explain", "deep_dive") and not state.lesson_confirmed
    if state.awaiting_topic:
        if wants_auto_content:
            # Режим «урок»/«объяснение»/«разбор»: авто-тема из subject/topic — не блокируем пользователя
            return NODE_CONTENT
        return END
    if wants_auto_content:
        return NODE_CONTENT
    return NODE_TUTOR_NEXT


def content_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Режимы «урок» / «объяснение» / «глубокий разбор» (7.3.4).

    Один узел: генерирует контент по режиму (lesson/explain/deep_dive), показывает его
    (tutor.lesson), затем спрашивает «готов(а) перейти к квизу?».
    """
    st = state.model_copy(deep=True)
    mode = st.mode or "lesson"
    if st.lesson_confirmed:
        return st.model_dump()

    if st.pending_answer is not None:
        from .agent_loop import _answer_free_question, _is_not_ready, _is_ready_to_quiz

        raw = st.pending_answer
        low = raw.strip().lower()
        st.pending_answer = None
        if _is_ready_to_quiz(raw):
            st.lesson_confirmed = True
            st.lesson_done = True
            st.agent_question = None
            _emit(deps, "system", message="Отлично! Начинаем квиз.", kind="lesson.done")
            return st.model_dump()
        # «Дополнить материал» / «Начать с нуля» (7.3): разводим команды.
        # Обе просят свежие источники, но:
        #   «Начать с нуля» — полностью забываем старый урок;
        #   «Дополнить материал» — старый урок остаётся, но после поиска
        #   новых источников контент пересобирается (lesson_rebuild).
        # Дальше граф уходит в find_textbook_node (force_source_refresh=True,
        # sources очищены → route_after_content → NODE_FIND_TEXTBOOK).
        if "с нуля" in low or "заново" in low:
            st.clear_lesson()
            st.lesson_rebuild = True
            st.force_source_refresh = True
            st.sources = []
            st.collection_id = None
            st.source_status = None
            st.agent_question = None
            _emit(deps, "system", message="Начинаю с нуля — ищу свежие материалы по теме.",
                  kind="lesson.repeat")
            return st.model_dump()
        if "дополнить" in low or "искать" in low or "найти" in low:
            st.lesson_rebuild = True
            st.force_source_refresh = True
            st.sources = []
            st.collection_id = None
            st.source_status = None
            st.agent_question = None
            _emit(deps, "system", message="Ищу дополнительные материалы по теме.",
                  kind="lesson.repeat")
            return st.model_dump()
        if _is_not_ready(raw):
            # Явный отказ («нет», «не готов», «пока нет») → сбрасываем и перегенерируем
            st.clear_lesson()
            st.agent_question = None
            _emit(deps, "system", message="Повторяем материал по теме.", kind="lesson.repeat")
        else:
            # Любой другой ответ (вопрос, просьба, короткая фраза) → отвечаем по RAG,
            # НЕ сбрасываем урок (иначе урок пересказывается заново — баг).
            st.agent_message = _answer_free_question(st, deps, raw)
            st.agent_question = None
            _emit(deps, "system", message=st.agent_message, kind="agent.message")
            return st.model_dump()

    # «Дополнить материал»: после поиска новых источников (sources обновлены) —
    # старый урок пересобирается заново из расширенного контекста, а не показывается как есть.
    if st.lesson_rebuild and (st.sources or st.collection_id or st.source_status == "ready"):
        logger.info("content_node: «дополнить» — пересборка урока после обновления источников")
        st.lesson_rebuild = False
        st.clear_lesson()
        st.lesson_done = False  # сбрасываем флаг — иначе _load_cached_lesson может вернуть старое и блокировать перегенерацию

    if st.lesson_text:
        # материал уже показан — ждём подтверждения
        st.lesson_done = True
        st.agent_question = "Готов(а) перейти к квизу? (да / нет)"
        _emit(deps, "intake.question", question=st.agent_question, missing_fields=["lesson_confirm"])
        return st.model_dump()

    # Тема (единый ключ): активный узел графа → его название, иначе subject/topic.
    topic = st.topic or st.subject or "общая тема"
    if st.active_topic:
        from .knowledge_graph import KnowledgeGraph as _KG2
        kg = _KG2.from_dict(st.knowledge_graph or {})
        title = _node_title(kg, st.active_topic)
        if title:
            topic = title

    # Кэш урока (3.1/7.2): повторное прохождение темы — показываем урок из прошлого раза,
    # не генерируя заново (пользователь сам решит, дополнять ли материал).
    # При пересборке ("дополнить материал") или явном запросе свежих источников ("с нуля")
    # кэш не подставляем — нужен свежий контент.
    if not st.lesson_rebuild and not getattr(st, "force_source_refresh", False):
        cached_lesson = _load_cached_lesson(st, deps, topic)
        if cached_lesson is not None:
            logger.info("content_node: кэшированный урок по теме «%s» — без генерации", topic)
            return _apply_cached_lesson(st, deps, cached_lesson, topic)

    # Генерируем материал по активной теме и режиму
    # deep_dive берёт больше контекста (несколько разделов, 7.3.4); lesson/explain — k=5
    k = 8 if mode == "deep_dive" else 5
    # Гранулярный прогресс (оптимизация): пользователь видит этап генерации, а не «тишину»
    _emit(deps, "source.progress", stage="content", url="", status="generating",
          message=f"Ищу материалы по теме «{topic}»…")
    chunks = _rag_chunks(deps.store, topic, st, k=k)
    # RAG-first гейт: без контекста контент не выдумываем — просим источник.
    if not chunks:
        st.lesson_done = False
        st.agent_message = (
            f"По теме «{topic}» пока нет материала в загруженных источниках. "
            "Загрузите учебник (PDF/DOCX) или нажмите «Найти учебник» — и я подготовлю материал."
        )
        # agent_question обязателен: иначе CLI/веб зациклится на «внутреннем шаге»
        st.agent_question = st.agent_message
        _emit(deps, "source.progress", stage="content", url="", status="empty",
              message=st.agent_message)
        _emit(deps, "system", message=st.agent_message, kind="content.empty")
        _emit(deps, "intake.question", question=st.agent_question, missing_fields=["textbook_file"])
        return st.model_dump()
    context = [_clean_text_lines(c.chunk.text) for c in chunks]
    # Метаданные источника параллельно context (честный groundedness); None для wiki.
    sources = [c.chunk.metadata() for c in chunks]
    # Баг #6: wiki-статьи (накопленные между сессиями) часто качественнее сырых чанков —
    # добавляем их ПЕРВЫМИ в контекст урока/объяснения.
    wiki_bodies = _wiki_articles_for(st, deps)
    if wiki_bodies:
        context = wiki_bodies + context
        sources = [None] * len(wiki_bodies) + sources
        logger.info("content_node: +%d wiki-статей к контексту темы «%s»", len(wiki_bodies), topic)
    _emit(deps, "source.progress", stage="content", url="", status="generating",
          message=f"Генерирую {_MODE_LABELS.get(mode, 'материал')} по теме «{topic}» ({len(context)} фрагментов)…")
    on_token = deps.on_token  # реальный стриминг токенов в браузер (stream=True)
    _ct_t0 = time.monotonic()
    if mode == "deep_dive":
        st.set_plain_lesson(tutor_mod.generate_deep_dive(topic, context, st, llm_call=deps.expert_llm, on_token=on_token))
        st.agent_message = "Глубокий разбор по теме готов. Можно задать вопрос или перейти к квизу."
    elif mode == "explain":
        st.set_plain_lesson(tutor_mod.generate_explanation(topic, context, st, llm_call=deps.tutor_llm, on_token=on_token))
        st.agent_message = "Объяснение по теме готово. Можно задать вопрос или перейти к квизу."
    else:
        # Урок — прямой стриминг markdown текста (без JSON-pipeline).
        # Стриминг с первого токена: пользователь видит прогресс сразу.
        if on_token is not None:
            _emit(deps, "source.progress", stage="content", url="", status="generating",
                  message=f"Генерирую урок по теме «{topic}»…")
        lesson = tutor_mod.generate_lesson(topic, context, st, llm_call=deps.tutor_llm, on_token=on_token, sources=sources)
        st.set_lesson(lesson)
        st.agent_message = "Урок по теме готов. Можно задать вопрос или перейти к квизу."
        # Кэш урока (3.1/7.2): сохраняем для повторного прохождения темы.
        _save_lesson_to_cache(st, deps, lesson, topic)
    logger.info("content_node: mode=%s topic=%r ctx=%d llm=%.3fs", mode, topic, len(context), time.monotonic() - _ct_t0)
    st.lesson_done = True
    # Ключевые понятия урока → wiki-статья темы (roadmap #3: drill-down в графе)
    try:
        from .wiki import KnowledgeWiki

        terms = [t.get("term") for t in st.lesson_key_terms if isinstance(t, dict) and t.get("term")]
        if terms:
            KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR,
                          student_id=getattr(st, "student_id", None) or "").sync_concepts(st, topic, terms)
    except Exception as exc:
        logger.warning("sync_concepts (ключевые понятия) не удался: %s", exc)
    _emit(deps, "tutor.lesson", **st.lesson_payload(topic))
    _emit(deps, "system", message=st.agent_message, kind="lesson.ready")

    # Student Knowledge Graph (roadmap #4): тема начата → in_progress
    student_id = getattr(st, "student_id", None) or ""
    if student_id:
        try:
            store = _student_kg(deps)
            if store is not None:
                store.update_knowledge_graph(
                    student_id=student_id,
                    subject=getattr(st, "subject", None) or "общая тема",
                    in_progress_topics=[{"topic_id": topic, "title": topic,
                                         "subject": getattr(st, "subject", None) or ""}],
                )
        except Exception as exc:
            logger.warning("Student KG mark_in_progress failed: %s", exc)

    # 7.3.3: в том же шаге задаём подтверждение перехода к квизу — без «зависшего» хода
    st.agent_question = "Готов(а) перейти к квизу? (да / нет)"
    _emit(deps, "intake.question", question=st.agent_question, missing_fields=["lesson_confirm"])
    return st.model_dump()


def route_after_content(state: TutorState) -> str:
    """После материала (урок/объяснение/разбор): подтверждён переход к квизу → квиз;
    «Дополнить»/«Начать с нуля» (force_source_refresh + очищены sources) → поиск свежих
    источников (find_textbook_node); иначе END."""
    if state.lesson_confirmed:
        return NODE_TUTOR_NEXT
    if state.force_source_refresh and not state.sources and not state.collection_id:
        return NODE_FIND_TEXTBOOK
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


def _index_failure(st: TutorState, deps: GraphDeps, err: Exception) -> TutorState:
    """Помечает источник failed и публикует source.failed (UI показывает ошибку + подсказку)."""
    st.source_status = "failed"
    st.source_note = f"Ошибка индексации: {err}"
    st.agent_message = (
        "Не удалось проиндексировать документ (похоже, сервис эмбеддингов недоступен). "
        "Попробуйте ещё раз чуть позже или нажмите «Найти учебник»."
    )
    _emit(deps, "source.failed", reason=str(err), message=st.agent_message)
    return st


def process_document_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    if st.textbook_scanned:
        # уже знаем, что это скан — ждём/обрабатываем страницы в других узлах
        return st.model_dump()
    if not st.textbook_file:
        return {"source_status": "failed", "source_note": "no file"}
    path = Path(st.textbook_file)
    source_name = st.textbook_name or path.name
    # Уровень 3: подтверждение намерения перед разбором/индексацией
    _emit(deps, "system", message=_intent_message(st), kind="intent")
    _emit(deps, "source.progress", stage="index", url="", status="indexing",
          message=f"Разбор документа {source_name}…")
    try:
        text = parse_document(path)
    except Exception as e:
        return _index_failure(st, deps, e).model_dump()

    if detect_text_layer(text, min_chars=deps.settings.OCR_MIN_TEXT_CHARS):
        st.textbook_scanned = True
        st.agent_question = (
            "Учебник сканированный (без текста). Открой учебник и укажи страницы нужной "
            "темы и саму тему (например: 12-15, Дроби). Или напиши «все» для полного распознавания."
        )
        st.agent_message = "Файл не содержит текстового слоя — распознаю по страницам."
        _emit(deps, "system", message=st.agent_message, kind="doc.scanned")
        return st.model_dump()

    try:
        stats = process_document(
            path, source=source_name, store=deps.store, subject=st.subject, grade=st.grade,
            student_id=getattr(st, "student_id", None) or None,
        )
        st.collection_id = stats["collection"]
        st.source_status = "ready"
        st.sources = [{"type": "file", "path": str(path), "num_chunks": stats["num_chunks"]}]
        st.source_note = f"Документ проиндексирован: {stats['num_chunks']} чанков"
        st.knowledge_graph = build_or_load_textbook_graph(
            text, source=source_name, path=path, graph_dir=deps.settings.KNOWLEDGE_GRAPH_DIR,
            llm_ontology=_ontology_llm_call(deps),
            student_id=getattr(st, "student_id", None) or None,
        ).to_dict()
    except Exception as e:
        return _index_failure(st, deps, e).model_dump()
    # Уровень 1: конкретная тема → гейт пропускается, сразу к уроку/квизу по ней
    _finalize_source(st, web_sources=False)
    _emit(deps, "source.progress", stage="index", url="", status="done",
          message=st.source_note)
    _emit(deps, "graph.ready", nodes=_topic_count(st.knowledge_graph.get("nodes", [])),
          edges=len(st.knowledge_graph.get("edges", [])))
    # Wiki-LLM (roadmap #2): фоновое извлечение фактов в статьи — не блокирует индекс
    _schedule_wiki_extraction(st, deps)
    return st.model_dump()


def route_doc_result(state: TutorState) -> str:
    """Маршрут после process_document: failed → стоп; скан → запрос страниц; индекс готов → гейт темы."""
    if state.source_status == "failed":
        return NODE_SOURCE_FAILED
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
            "2) название темы/урока. Ответь, например: «12-15, Дроби»."
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
    try:
        chunks = _make_chunks(text, source=source_name, subject=st.subject, grade=st.grade,
                              topic=st.topic, student_id=getattr(st, "student_id", None) or None)
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
            text, source=source_name, path=path, graph_dir=deps.settings.KNOWLEDGE_GRAPH_DIR,
            llm_ontology=_ontology_llm_call(deps),
            student_id=getattr(st, "student_id", None) or None,
        ).to_dict()
    except Exception as e:
        return _index_failure(st, deps, e).model_dump()
    # Уровень 1: конкретная тема → гейт пропускается
    _finalize_source(st, web_sources=False)
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

    # Уровень 3: подтверждение намерения перед поиском материалов
    _emit(deps, "system", message=_intent_message(st), kind="intent")
    _emit(deps, "source.progress", stage="catalog", url="", status="searching",
          message=f"Поиск материалов по теме «{st.topic or st.subject or ''}»…")
    # Ищем по теме. Если topic="all" («весь учебник») — ищем по предмету,
    # но передаём st.topic как контекст для LLM (не blank-им).
    search_topic = st.topic or st.subject or ""
    col = (deps.source_collector or source_finder.collect_source_materials)(
        subject=st.subject or "",
        topic=search_topic,
        grade=st.grade or "",
        author=st.textbook_author or "",
        settings=deps.settings,
        http=deps.http,
        student_id=st.student_id or "",
        use_cache=not st.force_source_refresh,
        allowed_domains=None if st.allow_any_sources else (st.source_whitelist or None),
        allow_any=st.allow_any_sources,
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
        # Источники без осмысленных чанков (слайд-шоу, пустые скрапы) не индексируем.
        # _make_chunks возвращает [] для страниц-презентаций — такие источники отпадают.
        # Качество чанков дополнительно проверяется на ретривеле (_chunk_has_meaningful_content).
        kept_sources: List[Dict[str, Any]] = []
        kept_texts: List[str] = []
        chunks: List[Any] = []
        for s, t in zip(col.sources, col.texts):
            sc = _make_chunks(t, source=s.get("url", "web"), subject=st.subject, grade=st.grade,
                              topic=st.topic, student_id=getattr(st, "student_id", None) or None)
            if not sc:
                logger.info("Источник %s: нет осмысленных чанков — пропускаем",
                            s.get("url", "web"))
                continue
            chunks.extend(sc)
            kept_sources.append(s)
            kept_texts.append(t)
        col.sources = kept_sources
        col.texts = kept_texts
        if not chunks:
            st.source_status = "failed"
            st.agent_message = (
                f"По теме «{st.topic or st.subject or ''}» источники оказались презентациями/фрагментами "
                "без связного текста. Загрузите учебник (PDF/DOCX) или попробуйте другой источник."
            )
            st.agent_question = st.agent_message
            _emit(deps, "source.progress", stage="content", url="", status="empty",
                  message=st.agent_message)
            _emit(deps, "system", message=st.agent_message, kind="content.empty")
            return st.model_dump()
        deps.store.add(chunks)
        st.collection_id = "web"
        st.sources = col.sources
        st.force_source_refresh = False  # свежий поиск выполнен — кэш снова разрешён
        
        # Сохраняем в кэш материалов (6.3). Ключ включает student_id — кэш
        # персональный по ученику, а не общий для всех.
        _sid2 = st.student_id or "anon"
        cache_key = f"{_sid2}::{st.subject}::{search_topic or ''}::{st.grade or ''}"
        try:
            from . import source_finder as _sf_cache
            _sf_cache._set_cached_materials(cache_key, col.sources, collection_id="web",
                                            cache_dir=deps.settings.SOURCES_CACHE_DIR)
        except Exception:
            logger.warning("Кэш материалов не сохранён: %s", cache_key, exc_info=True)
        st.source_status = "ready"
        st.source_note = f"Собрано материалов: {len(col.sources)} источников"
        _emit(deps, "source.progress", stage="index", url="", status="done",
              message=st.source_note)

        # Строим граф знаний из собранного веб-контента. Онтология (вершины+рёбра)
        # строится МОДЕЛЬЮ по объединённому контенту темы; при недоступности/мусоре —
        # эвристический каркас (по каждому источнику отдельно → объединение).
        try:
            from .knowledge_graph import KnowledgeGraph, build_model_graph

            root_source = st.topic or st.subject or "web"
            merged = "\n\n".join(t for t in col.texts if t and t.strip())
            kg = build_model_graph(merged, root_source, _ontology_llm_call(deps)) if merged else None
            if kg is not None:
                st.knowledge_graph = kg.to_dict()
            else:
                kg = KnowledgeGraph()
                root_id = f"book:{root_source}"
                kg.add_topic(root_id, f"Учебник «{root_source}»", node_type="book")
                for i, (s, t) in enumerate(zip(col.sources, col.texts)):
                    if not (t and t.strip()):
                        continue
                    sub = build_textbook_graph(t, source=f"{root_source}:{i}")
                    # узел-источник (страница): читаемое название из title или домен URL.
                    # allow_url=True — намеренный узел, а не мусорный заголовок из контента.
                    page_id = f"page:{root_source}:{i}"
                    page_title = _readable_title(s.get("url", "")) if s.get("url") else f"Источник {i + 1}"
                    # подтемы страницы (реальные заголовки, без generic «Тема «source»»)
                    sub_topics = [
                        (n, d) for n, d in sub.graph.nodes(data=True)
                        if d.get("type") != "book"
                        and not d.get("title", "").startswith(f"Тема «{root_source}:{i}")
                    ]
                    if sub_topics:
                        kg.add_topic(page_id, page_title, node_type="topic", allow_url=True,
                                     parent_id=root_id)
                        kg.add_edge(root_id, page_id, PART_OF)
                        for n, d in sub_topics:
                            kg.add_topic(n, d.get("title", n), node_type="topic",
                                         section_number=d.get("section_number"), parent_id=page_id)
                            kg.add_edge(page_id, n, PART_OF)
                    else:
                        # страница без структуры — сама становится темой
                        kg.add_topic(page_id, page_title, node_type="topic", allow_url=True,
                                     parent_id=root_id)
                        kg.add_edge(root_id, page_id, PART_OF)
                if kg.graph.number_of_nodes() <= 1:
                    # пусто — хотя бы один узел, чтобы панель тем не была пустой
                    kg.add_topic(f"topic:{root_source}", f"Тема «{root_source}»", node_type="topic")
                    kg.add_edge(root_id, f"topic:{root_source}", PART_OF)
                # LLM-фильтрация тем: отсекаем мусор, задания, класс-специфичные темы
                try:
                    from .knowledge_graph import filter_topics_with_llm

                    # Получаем список тем из графа
                    topic_titles = [
                        d.get("title", "")
                        for n, d in kg.graph.nodes(data=True)
                        if d.get("type") not in ("book", "page") and d.get("title")
                    ]
                    if topic_titles:
                        # Используем cheap-модель для фильтрации
                        cheap_llm = _cheap_llm_call(deps)
                        if cheap_llm:
                            filtered_titles = filter_topics_with_llm(topic_titles, cheap_llm)
                            # Обновляем граф: удаляем отфильтрованные темы
                            topics_to_remove = [
                                n for n, d in kg.graph.nodes(data=True)
                                if d.get("type") not in ("book", "page")
                                and d.get("title")
                                and d.get("title") not in filtered_titles
                            ]
                            for nid in topics_to_remove:
                                if nid in kg.graph:
                                    kg.graph.remove_node(nid)
                            logger.info(
                                "LLM-фильтрация: %d тем → %d валидных",
                                len(topic_titles), len(filtered_titles),
                            )
                except Exception as exc:
                    logger.warning("LLM-фильтрация тем не удалась: %s", exc)
                st.knowledge_graph = kg.to_dict()
            # Уровень 1: материалы собраны по теме → гейт пропускается, сразу к уроку/квизу
            _finalize_source(st, web_sources=True)
            _emit(deps, "graph.ready",
                  nodes=_topic_count(st.knowledge_graph.get("nodes", [])),
                  edges=len(st.knowledge_graph.get("edges", [])))
            # Wiki-LLM (roadmap #2): фоновое извлечение фактов в статьи
            _schedule_wiki_extraction(st, deps)
        except Exception as exc:
            logger.warning("Граф из веб-источников не построен: %s", exc)

        return st.model_dump()

    st.source_status = "failed"
    st.source_note = col.failed_reason or col.message
    # Действие вместо сухого «не найдены»: что сделать дальше (upload / уточнить тему).
    if col.failed_reason == "empty_result" or not (col.message or "").strip():
        topic = st.topic or st.subject or ""
        st.agent_message = (
            f"Материалы по теме «{topic}» не найдены. "
            "Загрузите учебник (PDF/DOCX) или нажмите «Найти учебник». "
            "Можно также уточнить формулировку темы."
        )
    else:
        st.agent_message = col.message
    _emit(deps, "source.failed", reason=st.source_note, message=st.agent_message)
    return st.model_dump()


def route_textbook_result(state: TutorState) -> str:
    if state.source_status == "failed":
        return NODE_SOURCE_FAILED
    if state.textbook_file:
        return NODE_PROCESS_DOCUMENT
    # Граф построен → через гейт темы. При конкретной теме гейт пропускается,
    # а route_after_topic_gate отправляет в урок/объяснение/квиз по ней (Уровень 1).
    if state.knowledge_graph or state.awaiting_topic:
        return NODE_TOPIC_GATE
    return NODE_TUTOR_NEXT


def source_failed_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    st.session_status = "failed"
    # Протухший урок/оценка из предыдущего запуска не должны «всплывать» рядом
    # с «материалы не найдены» (иначе в ленте противоречие: нет материала + есть урок).
    st.clear_lesson()
    st.agent_message = st.agent_message or "Материалы по теме не найдены. Предлагаем загрузить свой документ."
    return st.model_dump()


def route_tutor(state: TutorState) -> str:
    if state.quiz_complete or state.session_status == "failed":
        return NODE_SUMMARY
    if state.subtask_queue is not None:
        return NODE_SUBTASK
    if state.pending_answer is not None:
        return NODE_EVALUATE_ANSWER
    if state.current_question is None:
        return NODE_GENERATE_QUESTION
    return END  # активный вопрос ждёт ответа (в т.ч. после подсказки)


def route_tutor_agent(state: TutorState) -> str:
    """Роутер агентного квиза (7.3.1): все ходы идут через agent_tutor_node."""
    if state.quiz_complete or state.session_status in ("completed", "failed"):
        return NODE_SUMMARY
    return NODE_AGENT_TUTOR


def _maybe_emit_mastery_gate(st: TutorState, deps: GraphDeps, topic: str) -> None:
    """Mastery-гейт (DeepTutor): если пререквизиты темы не освоены — мягкое предупреждение."""
    student_id = getattr(st, "student_id", None) or ""
    if not student_id or not topic:
        return
    try:
        store = _student_kg(deps)
        if store is None:
            return
        kg = store.get_knowledge_graph(student_id)
        if kg is None:
            return
        gaps = kg.get_prerequisite_gaps(topic)
        if gaps:
            titles = [g for g in gaps]
            _emit(deps, "system",
                  message=f"Совет: прежде чем «{topic}», стоит повторить: {', '.join(titles)}.",
                  kind="mastery.gate", gaps=titles)
    except Exception as exc:
        logger.warning("mastery.gate failed: %s", exc)


def generate_question_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    st = state.model_copy(deep=True)
    review_bank = _review_bank(deps, st)
    if review_bank is not None:
        if st.review_requested and not st.review_active:
            due = review_bank.get_due(subject=getattr(st, "subject", None) or None,
                                      limit=getattr(deps.settings, "REVIEW_QUIZ_SIZE", 5))
            if due:
                st.review_active = True
                st.review_cards = [c.to_dict() for c in due]
                st.review_index = 0
                st.review_reviewed = 0
                st.review_correct = 0
                _emit(deps, "system", message=f"Повторяем {len(due)} карточк(и)…", kind="review.start")
            else:
                _emit(deps, "system", message="Карточек на повторение нет.", kind="review.empty")
            st.review_requested = False

        if st.review_active:
            if st.review_index < len(st.review_cards):
                rc = st.review_cards[st.review_index]
                cid = f"review:{rc['card_id']}"
                card = QuizCard(
                    question_id=cid, question=rc.get("question", ""),
                    options=rc.get("options"), answer_type=rc.get("answer_type", "open"),
                    difficulty=rc.get("difficulty", "medium"), topic=rc.get("topic", ""),
                )
                st.current_question = card
                st.current_answers = [rc.get("correct_answer", "")]
                st.agent_question = card.question
                st.agent_options = card.options
                st.records.append({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "question_id": cid, "question": card.question, "options": card.options,
                    "answer_type": card.answer_type, "difficulty": card.difficulty,
                    "topic": card.topic, "section": st.current_section,
                    "student_answer": None, "score01": None, "correct": None,
                    "feedback": None, "correct_answer": rc.get("correct_answer", ""),
                    "model_used": None, "judge_score": None, "review": True,
                })
                _emit(deps, "quiz.card", question_id=cid, question=card.question,
                      options=card.options, answer_type=card.answer_type,
                      difficulty=card.difficulty, topic=card.topic,
                      num_questions=len(st.review_cards), question_num=st.review_index + 1,
                      review=True)
                return st.model_dump()
            st.review_active = False
            st.review_cards = []
            st.review_index = 0
            _emit(deps, "review.done", reviewed=st.review_reviewed,
                  correct=st.review_correct, lapses=0)

    topic = st.topic or st.subject or "общая тема"
    # Единый ключ темы: при активном узле графа используем его НАЗВАНИЕ, а не широкий
    # st.topic — иначе card.topic (ключ knowledge_map/wiki) не совпадёт с title узла,
    # и мастерство не отобразится на узлах графа (см. topic_gate_node / select_topic).
    if st.active_topic and st.knowledge_graph:
        from .knowledge_graph import KnowledgeGraph as _KG
        kg = _KG.from_dict(st.knowledge_graph)
        title = _node_title(kg, st.active_topic)
        if title:
            topic = title
    if len(st.asked_questions) == 0:
        _maybe_emit_mastery_gate(st, deps, topic)
    _emit(deps, "source.progress", stage="quiz", url="", status="generating",
          message=f"Генерирую вопрос по теме «{topic}»…")
    _gq_t0 = time.monotonic()
    chunks = _rag_chunks(deps.store, topic, st, k=3)
    _gq_t_rag = time.monotonic() - _gq_t0
    context = [_clean_text_lines(c.chunk.text) for c in chunks]
    if not context:
        context = ["Нет контекста по теме."]
    # Антидубликат (7.3.2): тексты уже заданных вопросов → в промпт; при семантическом
    # совпадении с заданным регенерируем (≤ QUESTION_DEDUPE_RETRIES раз), затем принимаем.
    prev_asked = list(st.asked_questions)
    retries = getattr(deps.settings, "QUESTION_DEDUPE_RETRIES", 2)
    threshold = getattr(deps.settings, "QUESTION_DEDUPE_THRESHOLD", 0.85)
    card = None
    _gq_llm_t = 0.0
    _gq_dup_t = 0.0
    for attempt in range(retries + 1):
        _t_llm = time.monotonic()
        card = tutor_mod.generate_question(
            topic, context, st.difficulty, st, llm_call=deps.tutor_llm
        )
        _gq_llm_t += time.monotonic() - _t_llm
        _t_dup = time.monotonic()
        is_dup = bool(prev_asked) and tutor_mod.is_duplicate_question(
            deps.embedder, card.question, prev_asked, threshold
        )
        _gq_dup_t += time.monotonic() - _t_dup
        if not is_dup:
            break
        st.asked_questions.pop()  # откатываем дубль перед регенерацией
    _gq_total = time.monotonic() - _gq_t0
    logger.info(
        "generate_question: topic=%r context=%d rag=%.3fs llm=%.3fs dedupe=%.3fs total=%.3fs",
        topic, len(context), _gq_t_rag, _gq_llm_t, _gq_dup_t, _gq_total,
    )
    from .observability import log_graph_node

    log_graph_node(
        deps, "generate_question.step",
        note=f"topic={topic} ctx={len(context)} rag={_gq_t_rag:.3f}s llm={_gq_llm_t:.3f}s dedupe={_gq_dup_t:.3f}s",
        duration=_gq_total,
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
        "correct_answer": ", ".join(st.current_answers) or None,
        "model_used": None,
        "judge_score": None,
    })
    _emit(deps, "quiz.card", question_id=card.question_id, question=card.question,
          options=card.options, answer_type=card.answer_type, difficulty=card.difficulty,
          topic=card.topic, num_questions=st.num_questions,
          question_num=st.answered_count + 1)
    return st.model_dump()


def evaluate_answer_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    from .evaluation import evaluate_and_record

    st = state.model_copy(deep=True)
    card = st.current_question
    answer = st.pending_answer or ""
    st, message, _judge, _expl = evaluate_and_record(
        st, deps, card, answer, emit=lambda ev, **kw: _emit(deps, ev, **kw)
    )
    return st.model_dump()


def subtask_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Декомпозиция (B6): пошаговый разбор сложной задачи.

    Маршрутизируется из route_tutor при subtask_queue is not None. Обрабатывает
    по одному шагу за invoke:
      - ответ короче 3 символов → переспрашиваем текущий шаг;
      - нормальный ответ → продвигаем subtask_index;
      - очередь исчерпана → исходный вопрос перезадаётся (новый question_id "-b").
    """
    st = state.model_copy(deep=True)
    if not st.subtask_queue:
        return st.model_dump()
    current = st.subtask_queue[st.subtask_index] if 0 <= st.subtask_index < len(st.subtask_queue) else st.subtask_queue[-1]
    if st.pending_answer is not None:
        ans = st.pending_answer.strip()
        st.pending_answer = None
        if len(ans) < 3:
            st.agent_question = f"Коротко ответь на шаг: {current}"
            return st.model_dump()
        st.subtask_index += 1
        st.subtask_answer_ok = True
    if st.subtask_index >= len(st.subtask_queue):
        # Разбор завершён: возвращаемся к исходному вопросу (он не финализировался)
        st.subtask_queue = None
        st.subtask_index = 0
        card = st.current_question
        new_qid = f"{card.question_id}-b" if card else ""
        new_card = card.model_copy(update={"question_id": new_qid})
        st.current_question = new_card
        st.agent_question = new_card.question
        st.agent_options = new_card.options
        st.records.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "question_id": new_qid,
            "question": new_card.question,
            "options": new_card.options,
            "answer_type": new_card.answer_type,
            "difficulty": new_card.difficulty,
            "topic": new_card.topic,
            "section": st.current_section,
            "student_answer": None,
            "score01": None,
            "correct": None,
            "feedback": None,
            "correct_answer": ", ".join(st.current_answers) or None,
            "model_used": None,
            "judge_score": None,
        })
        _emit(deps, "quiz.card", question_id=new_card.question_id, question=new_card.question,
              options=new_card.options, answer_type=new_card.answer_type, difficulty=new_card.difficulty,
              topic=new_card.topic, num_questions=st.num_questions,
              question_num=st.answered_count + 1)
        return st.model_dump()
    current = st.subtask_queue[st.subtask_index]
    _emit(deps, "quiz.hint", hint=current, level=0, attempts_left=0,
          subtask=True, subtask_index=st.subtask_index + 1, subtask_total=len(st.subtask_queue))
    st.agent_question = current
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

    # Автоматический экспорт результатов при завершении квиза
    try:
        session_id = getattr(st, 'session_id', '') or ''
        if st.records:
            write_session_exports(st, session_id=session_id)
            logger.info("Auto-export: questions + summary CSV saved")
    except Exception as exc:
        logger.warning("Auto-export CSV failed: %s", exc)

    try:
        from .okf import emit_okf_bundle

        source_name = st.textbook_name or st.subject or 'session'
        session_id = getattr(st, 'session_id', '') or ''
        okf_dir = Path(deps.settings.KNOWLEDGE_GRAPH_DIR).parent / "okf" / session_id
        emit_okf_bundle(st, out_dir=okf_dir, source_name=source_name)
        logger.info("Auto-export: OKF bundle saved")
    except Exception as exc:
        logger.warning("Auto-export OKF failed: %s", exc)

    # Knowledge Wiki (roadmap #2): синхронизация mastery (идемпотентно, без attempts++)
    wiki = None
    try:
        from .wiki import KnowledgeWiki

        wiki = KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR,
                             student_id=getattr(st, "student_id", None) or "")
        updated = wiki.sync_mastery(st)
        if updated:
            logger.info("Knowledge Wiki: синхронизировано %d статей", len(updated))
            _emit(deps, "wiki.updated", subjects=[getattr(st, "subject", None) or "общая тема"],
                  count=len(updated))
    except Exception as exc:
        logger.warning("Knowledge Wiki update failed: %s", exc)

    # Student Knowledge Graph (roadmap #4): синхронизация knowledge_map + wiki → KG
    student_id = getattr(st, "student_id", None) or ""
    if student_id:
        try:
            store = _student_kg(deps)
            if store is not None:
                subject = getattr(st, "subject", None) or "общая тема"

                # Получаем wiki-статьи для этого предмета (для weak_areas/attempts)
                wiki_articles = []
                if wiki is not None:
                    try:
                        wiki_articles = [a.to_dict() for a in wiki.list_articles(subject)]
                    except Exception:
                        pass

                # knowledge_map из квиза + wiki articles → batch update KG
                store.update_knowledge_graph(
                    student_id=student_id,
                    subject=subject,
                    wiki_articles=wiki_articles,
                    knowledge_map=km,  # knowledge_map из summary_node
                )
                logger.info("Student KG: обновлено тем (student_id=%s, subject=%s)", student_id, subject)

                kg_obj = store.get_knowledge_graph(student_id)
                if kg_obj is not None:
                    kg_obj.sync_relations_from_knowledge_graph(
                        (st.knowledge_graph or {}).get("nodes", []),
                        (st.knowledge_graph or {}).get("edges", []),
                    )
                    store.save_knowledge_graph(student_id, kg_obj)
        except Exception as exc:
            logger.warning("Student Knowledge Graph update failed: %s", exc)

    return st.model_dump()


# ----------------------------------------------------------------------
# Сборка графа
# ----------------------------------------------------------------------
def _logged_node(deps: GraphDeps, name: str, fn: Callable[[TutorState], Dict[str, Any]]):
    """Обёртка узла: логирует проход узла (этап) в JSONL, если настроен step_logger."""

    def _wrapped(state: TutorState) -> Dict[str, Any]:
        _t0 = time.monotonic()
        try:
            result = fn(state)
            _obs_log_node(deps, name, duration=time.monotonic() - _t0)
            return result
        except Exception:
            _obs_log_node(deps, name, status="error", duration=time.monotonic() - _t0)
            raise

    return _wrapped


def build_graph(deps: Optional[GraphDeps] = None, checkpointer: Any = None) -> Any:
    deps = deps or make_graph_deps()

    g = StateGraph(TutorState)

    g.add_node("intake_node", _logged_node(deps, "intake_node", lambda s: intake_node(s, deps)))
    g.add_node("agent_intake_node", _logged_node(deps, "agent_intake_node", lambda s: agent_intake_node(s, deps)))
    g.add_node(NODE_SOURCE_ENTRY, _logged_node(deps, NODE_SOURCE_ENTRY, lambda s: source_entry(s, deps)))
    g.add_node(NODE_PROCESS_DOCUMENT, _logged_node(deps, NODE_PROCESS_DOCUMENT, lambda s: process_document_node(s, deps)))
    g.add_node(NODE_FIND_TEXTBOOK, _logged_node(deps, NODE_FIND_TEXTBOOK, lambda s: find_textbook_node(s, deps)))
    g.add_node(NODE_SOURCE_FAILED, _logged_node(deps, NODE_SOURCE_FAILED, lambda s: source_failed_node(s, deps)))
    g.add_node(NODE_WAIT_FOR_UPLOAD, _logged_node(deps, NODE_WAIT_FOR_UPLOAD, lambda s: wait_for_upload_node(s, deps)))
    g.add_node(NODE_REUSE_GATE, _logged_node(deps, NODE_REUSE_GATE, lambda s: reuse_materials_node(s, deps)))
    g.add_node(NODE_TOPIC_GATE, _logged_node(deps, NODE_TOPIC_GATE, lambda s: topic_gate_node(s, deps)))
    g.add_node(NODE_CONTENT, _logged_node(deps, NODE_CONTENT, lambda s: content_node(s, deps)))
    g.add_node(NODE_ASK_PAGE_RANGE, _logged_node(deps, NODE_ASK_PAGE_RANGE, lambda s: ask_page_range_node(s, deps)))
    g.add_node(NODE_HANDLE_DOC_PAGES, _logged_node(deps, NODE_HANDLE_DOC_PAGES, lambda s: handle_doc_pages_node(s, deps)))
    g.add_node(NODE_TUTOR_NEXT, _logged_node(deps, NODE_TUTOR_NEXT, lambda s: {}))
    g.add_node(NODE_AGENT_TUTOR, _logged_node(deps, NODE_AGENT_TUTOR, lambda s: agent_tutor_node(s, deps)))
    g.add_node(NODE_GENERATE_QUESTION, _logged_node(deps, NODE_GENERATE_QUESTION, lambda s: generate_question_node(s, deps)))
    g.add_node(NODE_EVALUATE_ANSWER, _logged_node(deps, NODE_EVALUATE_ANSWER, lambda s: evaluate_answer_node(s, deps)))
    g.add_node(NODE_SUBTASK, _logged_node(deps, NODE_SUBTASK, lambda s: subtask_node(s, deps)))
    g.add_node(NODE_SUMMARY, _logged_node(deps, NODE_SUMMARY, lambda s: summary_node(s, deps)))

    use_agent_intake = getattr(deps.settings, "USE_AGENT_INTAKE", True)
    if use_agent_intake:
        g.add_edge(START, "agent_intake_node")
        g.add_conditional_edges(
            "agent_intake_node",
            route_after_agent_intake,
            {END: END, NODE_SOURCE_ENTRY: NODE_SOURCE_ENTRY},
        )
    else:
        g.add_edge(START, "intake_node")
        g.add_conditional_edges("intake_node", after_intake, {END: END, NODE_SOURCE_ENTRY: NODE_SOURCE_ENTRY})
    g.add_conditional_edges(
        NODE_SOURCE_ENTRY,
        route_source,
        {
            NODE_SOURCE_FAILED: NODE_SOURCE_FAILED,
            NODE_TOPIC_GATE: NODE_TOPIC_GATE,
            NODE_PROCESS_DOCUMENT: NODE_PROCESS_DOCUMENT,
            NODE_FIND_TEXTBOOK: NODE_FIND_TEXTBOOK,
            NODE_WAIT_FOR_UPLOAD: NODE_WAIT_FOR_UPLOAD,
            NODE_REUSE_GATE: NODE_REUSE_GATE,
        },
    )
    g.add_conditional_edges(
        NODE_REUSE_GATE,
        route_reuse,
        {
            END: END,
            NODE_TOPIC_GATE: NODE_TOPIC_GATE,
            NODE_FIND_TEXTBOOK: NODE_FIND_TEXTBOOK,
        },
    )
    g.add_conditional_edges(
        NODE_TOPIC_GATE,
        route_after_topic_gate,
        {END: END, NODE_CONTENT: NODE_CONTENT, NODE_TUTOR_NEXT: NODE_TUTOR_NEXT},
    )
    g.add_conditional_edges(
        NODE_CONTENT,
        route_after_content,
        {END: END, NODE_TUTOR_NEXT: NODE_TUTOR_NEXT, NODE_FIND_TEXTBOOK: NODE_FIND_TEXTBOOK},
    )
    g.add_edge(NODE_WAIT_FOR_UPLOAD, END)
    g.add_conditional_edges(
        NODE_PROCESS_DOCUMENT,
        route_doc_result,
        {
            NODE_SOURCE_FAILED: NODE_SOURCE_FAILED,
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
            NODE_TOPIC_GATE: NODE_TOPIC_GATE,
            NODE_TUTOR_NEXT: NODE_TUTOR_NEXT,
        },
    )
    g.add_edge(NODE_SOURCE_FAILED, END)
    use_agent_tutor = getattr(deps.settings, "USE_AGENT_TUTOR", True)
    if use_agent_tutor:
        # Агент в квизе (7.3.1): все ходы тьюторинга идут через agent_tutor_node
        g.add_conditional_edges(
            NODE_TUTOR_NEXT,
            route_tutor_agent,
            {NODE_AGENT_TUTOR: NODE_AGENT_TUTOR, NODE_SUMMARY: NODE_SUMMARY},
        )
        g.add_conditional_edges(
            NODE_AGENT_TUTOR,
            route_after_agent_tutor,
            {END: END, NODE_SUMMARY: NODE_SUMMARY},
        )
    else:
        g.add_conditional_edges(
            NODE_TUTOR_NEXT,
            route_tutor,
            {
                NODE_GENERATE_QUESTION: NODE_GENERATE_QUESTION,
                NODE_EVALUATE_ANSWER: NODE_EVALUATE_ANSWER,
                NODE_SUBTASK: NODE_SUBTASK,
                NODE_SUMMARY: NODE_SUMMARY,
                END: END,
            },
        )
        g.add_edge(NODE_GENERATE_QUESTION, END)
        g.add_edge(NODE_EVALUATE_ANSWER, NODE_TUTOR_NEXT)
        g.add_edge(NODE_SUBTASK, END)
    g.add_edge(NODE_SUMMARY, END)

    return g.compile(checkpointer=checkpointer)
