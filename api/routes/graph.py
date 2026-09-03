"""Граф знаний учебника: GET /graph, related-узлы, выбор темы, OKF-экспорт."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("edututor.api.routes.graph")

from src.config import settings as default_settings
from src.knowledge_graph import KnowledgeGraph
from src.okf import emit_okf_bundle, validate_bundle

from ..deps import get_session, get_store
from ..engine import SessionStore, run_step

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["graph"])

# Узлы графа вида «Урок 3: Атмосфера» / «Параграф 5. Погода» должны матчиться
# с wiki-статьями по теме «Атмосфера» / «Погода» (иначе мастерство не окрашивает узлы).
_PREFIX_RE = re.compile(
    r"^(?:урок|параграф|lesson|section|module|unit|тема|раздел)\s*\d+[.:\s—–-]*\s*",
    re.IGNORECASE,
)


def _norm_topic(title: str) -> str:
    t = _PREFIX_RE.sub("", title or "").strip()
    return re.sub(r"\s+", " ", t).lower()


def _match_article(wiki, subject: str, title: str):
    """Точный матч, затем нормализованный («Урок 3: Атмосфера» → «атмосфера»)."""
    if not title:
        return None
    art = wiki.get(subject, title) if subject else None
    if art is not None:
        return art
    nt = _norm_topic(title)
    if not nt:
        return None
    art = wiki.get(subject, nt) if subject else None
    if art is not None:
        return art
    for a in wiki.list_articles():
        if _norm_topic(a.title) == nt:
            return a
    return None

# Фоновые шаги графа (fire-and-forget): держим ссылку, чтобы задача не была
# собрана GC до завершения (asyncio.create_task). Завершённые задачи удаляются
# через done-callback — модульный set не копит мусор (fix #6: утечка в dev/hot-reload).
_bg_tasks: set = set()
_MAX_BG_TASKS = 32


def _track_background_task(task: asyncio.Task) -> None:
    """Регистрируем фоновую задачу с автозачисткой завершённых.

    Помимо done-callback (не растёт в памяти), ограничиваем общее число
    незавершённых задач: при переполнении отменяем старейшую — защита от
    «накрутки» тем двойными кликами/перезагрузкой страницы.
    """
    task._bg_created_at = time.monotonic()
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    while len(_bg_tasks) > _MAX_BG_TASKS:
        stale = min(_bg_tasks, key=lambda t: getattr(t, "_bg_created_at", 0))
        _bg_tasks.discard(stale)
        if not stale.done():
            stale.cancel()


async def _run_step_background(session) -> None:
    """Фоновый шаг графа — результат публикуется через WS-очередь (оптимизация #2).

    HTTP POST /topic больше не ждёт полного завершения графа: пользователь сразу
    получает ответ, а прогресс/токены/вопрос приходят через WebSocket.
    """
    task = asyncio.current_task()
    try:
        await run_step(session)
    except asyncio.CancelledError:
        pass
    except Exception as e:  # pragma: no cover — ошибка фона не должна теряться
        logger.exception("Background run_step error: %s", e)
        from api.schemas import WsEvent

        session.queue.put(WsEvent(event="session.error", data={"message": str(e)}))
    finally:
        if task is not None:
            _bg_tasks.discard(task)


class TopicBody(BaseModel):
    topic_id: str


@router.get("/graph")
def get_graph(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    kg = KnowledgeGraph.from_dict(session.state.knowledge_graph)
    nodes = kg.to_dict()["nodes"]
    edges = kg.to_dict()["edges"]

    # Отсев мусорных тем (поисковая выдача/навигация) — защита и для кэшированных графов.
    from src.knowledge_graph import is_junk_topic

    clean_ids = {n["id"] for n in nodes
                 if n.get("type") == "book" or not is_junk_topic(n.get("title", ""))}
    if len(clean_ids) != len(nodes):
        nodes = [n for n in nodes if n["id"] in clean_ids]
        edges = [e for e in edges if e["source"] in clean_ids and e["target"] in clean_ids]

    # Mastery overlay (roadmap #3): цвет узла = уровень усвоения из Knowledge Wiki.
    # Матчим по названию темы/раздела (с нормализацией «Урок N: Тема» ≈ «Тема»),
    # subject сессии — фильтр.
    try:
        from src.wiki import KnowledgeWiki

        wiki = KnowledgeWiki(default_settings.KNOWLEDGE_WIKI_DIR,
                             student_id=getattr(session.state, "student_id", None) or "")
        subject = session.state.subject
        for n in nodes:
            title = n.get("title", "")
            art = _match_article(wiki, subject, title) if title else None
            if art is not None:
                n["mastery"] = art.mastery
                n["attempts"] = art.attempts
                n["correct"] = art.correct
                n["accuracy"] = art.accuracy
    except (OSError, IOError, FileNotFoundError):
        pass  # wiki недоступен — граф без mastery

    return {
        "nodes": nodes,
        "edges": edges,
        "active_topic": session.state.active_topic,
        "stats": kg.stats(),
    }


@router.get("/graph/{node_id}/related")
def related_nodes(session_id: str, node_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    kg = KnowledgeGraph.from_dict(session.state.knowledge_graph)
    node = next((n for n in kg.to_dict()["nodes"] if n.get("id") == node_id), None)
    return {
        "node": node,
        "related": kg.neighbors(node_id, max_depth=2),
    }


@router.get("/graph/{node_id}/wiki")
def node_wiki(session_id: str, node_id: str, store: SessionStore = Depends(get_store)):
    """Drill-down (roadmap #3): wiki-статья узла графа (mastery, заметки, тело)."""
    session = get_session(store, session_id)
    kg = KnowledgeGraph.from_dict(session.state.knowledge_graph)
    node = next((n for n in kg.to_dict()["nodes"] if n.get("id") == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="Узел не найден")
    title = node.get("title", "")
    try:
        from src.wiki import KnowledgeWiki

        wiki = KnowledgeWiki(default_settings.KNOWLEDGE_WIKI_DIR,
                             student_id=getattr(session.state, "student_id", None) or "")
        art = _match_article(wiki, session.state.subject, title) if title else None
        if art is not None:
            return {"node": node, "wiki": art.to_dict()}
    except (OSError, IOError, FileNotFoundError):
        pass
    return {"node": node, "wiki": None}


@router.post("/topic")
async def select_topic(session_id: str, body: TopicBody, store: SessionStore = Depends(get_store)):
    """Подготовка по теме: активируем узел графа, генерируем вопрос по этому разделу."""
    try:
        session = get_session(store, session_id)
        logger.info("select_topic: session=%s, topic_id=%s", session_id, body.topic_id)
        
        # Получаем title для сообщения
        title = ""
        kg = session.state.knowledge_graph or {}
        nodes = kg.get("nodes", []) if kg else []
        logger.info("select_topic: knowledge_graph has %d nodes", len(nodes))
        
        for n in nodes:
            if n.get("id") == body.topic_id:
                title = n.get("title", "")
                break
        if not title:
            raise HTTPException(status_code=404, detail="Тема не найдена в графе знаний")
        
        # Эмитим событие что тема выбрана (для WebSocket)
        from api.schemas import WsEvent
        try:
            if session.queue:
                session.queue.put(WsEvent(event="system", data={"message": f"Готовимся по теме: {title}..."}))
                logger.info("Emitted system event for topic selection")
            else:
                logger.warning("session.queue is None, skipping event emission")
        except Exception as e:
            logger.warning("Failed to emit event: %s", e)
        
        session.state = session.state.model_copy(
            update={
                "active_topic": body.topic_id,
                "topic": title or session.state.topic,
                "awaiting_topic": False,
            }
        )
        session.history.append({"role": "system", "text": f"Подготовка по теме: {title}"})

        # Гвард от двойного запуска (fix #1): если шаг графа уже выполняется —
        # отклоняем 409, иначе два фоновых run_step мутируют session.state параллельно.
        if session.step_active:
            logger.warning(
                "select_topic: step already active for session %s — rejecting %s",
                session_id, body.topic_id,
            )
            raise HTTPException(
                status_code=409,
                detail="Идёт подготовка предыдущей темы. Дождитесь завершения.",
            )

        # Fire-and-forget (оптимизация #2): HTTP не ждёт генерации вопроса/урока.
        # Результат и прогресс приходят через WS (source.progress, token, quiz.card,
        # tutor.lesson) — UI не «зависает» на 30-120 сек.
        # Закрываем окно между проверкой и стартом задачи: помечаем шаг активным
        # синхронно (run_step сбросит флаг в finally), чтобы два concurrent POST
        # не прошли гвард (fix #1).
        session.step_active = True
        task = asyncio.create_task(_run_step_background(session))
        _track_background_task(task)
        logger.info("Topic prepared in background for session %s", session_id)

        return {
            "ok": True,
            "active_topic": session.state.active_topic,
            "title": title,
        }
    except Exception as e:
        logger.exception("select_topic error: %s", e)
        raise


@router.post("/review")
async def start_review(session_id: str, store: SessionStore = Depends(get_store)):
    """Запустить блиц-опрос по должным карточкам SM-2 (по запросу ученика)."""
    session = get_session(store, session_id)
    if session.step_active:
        raise HTTPException(status_code=409, detail="Шаг уже выполняется")

    from src.review import ReviewBank

    student_id = getattr(session.state, "student_id", None) or ""
    due_count = 0
    try:
        bank = ReviewBank(default_settings.REVIEW_BANK_DIR, student_id)
        due_count = len(bank.get_due(limit=50))
    except (OSError, IOError, FileNotFoundError):
        due_count = 0

    session.state = session.state.model_copy(
        update={"review_requested": True, "agent_message": None, "pending_answer": None}
    )

    # Закрываем окно между проверкой и стартом задачи (fix #1: без двойного run_step)
    session.step_active = True
    task = asyncio.create_task(_run_step_background(session))
    _track_background_task(task)
    return {"ok": True, "due_count": due_count}


@router.get("/knowledge-package")
def knowledge_package(session_id: str, store: SessionStore = Depends(get_store)):
    """OKF-бандл знаний учебника (index + log + topics/*.md с YAML-frontmatter)."""
    session = get_session(store, session_id)
    out_dir = Path(default_settings.KNOWLEDGE_GRAPH_DIR).parent / "okf" / session_id
    source_name = session.state.textbook_name or (session.state.sources[0].get("path") if session.state.sources else "book")
    bundle = emit_okf_bundle(
        session.state, out_dir, Path(str(source_name)).name,
        subject=session.state.subject, grade=session.state.grade,
        curriculum=session.state.curriculum,
    )
    validation = validate_bundle(bundle)
    return {
        "okf_version": "0.2",
        "dir": str(bundle),
        "conformant": validation["conformant"],
        "errors": validation["errors"],
        "files": validation["files"],
        "index": (bundle / "index.md").read_text(encoding="utf-8"),
    }


class JudgeBody(BaseModel):
    """Запрос судьи-оценщика: target=lesson|question (+ question_id для вопроса)."""

    target: str  # "lesson" | "question"
    question_id: Optional[str] = None


@router.post("/judge")
def judge_session(session_id: str, body: JudgeBody, store: SessionStore = Depends(get_store)):
    """Судья-оценщик по запросу (кнопка в UI): урок или вопрос квиза.

    Синхронный HTTP: ждём LLM-судью (~5-10с) и возвращаем результат сразу;
    дополнительно публикуем WS-событие system kind="judge.result" для ленты.
    """
    from src.judge import judge_lesson, judge_quiz_question
    from src.llm_client import LLMClient

    session = get_session(store, session_id)
    target = (body.target or "").strip().lower()
    if target not in ("lesson", "question"):
        raise HTTPException(422, "target должен быть 'lesson' или 'question'")
    # NB: judge НЕ проверяет step_active — это read-only LLM-оценка,
    # не мутирующая граф. Пользователь может оценить урок в любой момент.

    st = session.state
    def _run() -> Dict[str, Any]:
        deps_judge = getattr(session.deps, "judge_llm", None)
        if deps_judge is not None:
            judge_call = deps_judge
        else:
            client = LLMClient(role="judge")
            judge_call = lambda msgs: client.chat(msgs, temperature=0.0, max_tokens=250).content or ""
        if target == "lesson":
            topic = st.active_topic or st.topic or st.subject or "тема"
            from src.graph import _rag_chunks

            chunks = _rag_chunks(session.deps.store, topic, st, k=3)
            context = [c.chunk.text for c in chunks]
            result = judge_lesson(
                st.lesson_text or "",
                context,
                st.grade,
                judge_call=judge_call,
                eval_criteria=(st.lesson_eval or {}).get("criteria"),
            )
        else:
            card = st.current_question
            if card is None:
                raise HTTPException(404, "Нет активного вопроса для оценки")
            # question_id: конкретный вопрос (если карточка сменилась) или текущий
            if body.question_id and card.question_id != body.question_id:
                raise HTTPException(404, "Вопрос уже неактивен — обновите карточку")
            result = judge_quiz_question(
                card.question,
                card.topic,
                st.grade,
                answer_type=card.answer_type,
                options=card.options or [],
                correct_answers=list(st.current_answers or []),
                difficulty=card.difficulty,
                judge_call=judge_call,
            )
        criteria = {k: round(float(v) / 10.0, 3) for k, v in result.criteria.items()}
        return {
            "target": target,
            "question_id": body.question_id,
            "criteria": criteria,
            "avg_score": round(float(result.avg_score) / 10.0, 3),
            "verdict": result.verdict,
        }

    try:
        result = _run()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("judge %s: %s", target, exc)
        raise HTTPException(502, f"Судья недоступен: {exc}")

    # WS-событие (для ленты/других клиентов); HTTP-ответ — синхронный источник правды
    try:
        from api.schemas import WsEvent

        session.queue.put(WsEvent(event="system", data={
            "message": f"Оценка «{target}»: {result['avg_score'] * 10:.0f}/10",
            "kind": "judge.result",
            "judge": result,
        }))
    except Exception as e:
        logger.debug("judge WS event skipped: %s", e)

    # Сохраняем результат судьи урока в состояние (для повторного показа при resync)
    if target == "lesson":
        try:
            st2 = session.state.model_copy(deep=True)
            st2.lesson_judge = {k: result[k] for k in ("criteria", "avg_score", "verdict")}
            session.state = st2
            if session.store and getattr(session.store, "_sqlite", None):
                session.store._save_state(session.id, st2)
        except Exception as e:
            logger.warning("Failed to persist lesson judge state: %s", e)

    return result
