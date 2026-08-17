"""
EduTutor — API-движок (раздел 8.4).

SessionStore: создание сессий, состояние TutorState, per-session asyncio-очередь
событий. run_step: исполнение шага графа в worker-потоке (asyncio.to_thread) —
не блокирует event loop; события публикуются потокобезопасно (queue.Queue).
POST /cancel: флаг cooperative-cancellation (проверка перед следующим шагом).
SQLite persistence: опциональная прослойка для сохранения состояния между перезапусками.
Предгенерация вопросов пакетами (batch pool).
"""

from __future__ import annotations

import asyncio
import logging
import queue as std_queue
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from api.schemas import IntakeStatusResponse, MessageResponse, QuizCard, WsEvent
from src.graph import GraphDeps, build_graph, make_graph_deps
from src.intake import compute_missing
from src.states import TutorState

logger = logging.getLogger("edututor.api")

# Idle-таймаут WS: должен переживать длинную обработку (парсинг+индексация ~1-2 мин)
WS_IDLE_TIMEOUT_SEC = 300


@dataclass
class SessionData:
    id: str
    state: TutorState
    deps: GraphDeps
    graph: Any
    queue: "std_queue.Queue[WsEvent]" = field(default_factory=std_queue.Queue)
    history: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    cancel_event: threading.Event = field(default_factory=threading.Event)
    last_activity: float = field(default_factory=time.monotonic)
    store: Optional["SessionStore"] = None  # reference to parent store for persistence


class SessionStore:
    """Хранилище сессий. deps — базовые зависимости (embedder/store переиспользуются).

    Устаревшие сессии (бездействие > SESSION_IDLE_TTL_SEC) удаляются при создании
    новых — сервер не копит мусор.
    
    Опционально: SQLite-прослойка для персистентности состояний.
    """

    def __init__(self, deps: Optional[GraphDeps] = None,
                 sqlite_store: Any = None, ttl: float = 1800.0):
        from src.session_store import SessionSQLiteStore
        
        self._base_deps = deps or make_graph_deps()
        self._sessions: Dict[str, SessionData] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        
        # SQLite persistence по умолчанию
        if sqlite_store is None:
            try:
                from src.config import settings as default_settings
                persist_path = Path(default_settings.SOURCES_CACHE_DIR).parent / "session_persist.db"
                persist_path.parent.mkdir(parents=True, exist_ok=True)
                self._sqlite = SessionSQLiteStore(persist_path, ttl_sec=self._ttl)
                logger.info("SQLite persistence initialized: %s", persist_path)
            except Exception as e:
                logger.warning("Не удалось инициализировать SQLite — отключаем персистентность: %s", e)
                self._sqlite = None
        else:
            self._sqlite = sqlite_store
    
    def _save_state(self, sid: str, st: TutorState) -> None:
        """Сохранить состояние в SQLite после каждого шага."""
        if self._sqlite is not None:
            try:
                self._sqlite.save(sid, st.model_dump())
            except Exception:
                logger.exception("Ошибка сохранения %s", sid)

    def create(self, initial: Optional[Dict[str, Any]] = None) -> SessionData:
        self._sweep()
        sid = uuid.uuid4().hex[:12]
        queue: "std_queue.Queue[WsEvent]" = std_queue.Queue()
        deps = replace(self._base_deps, on_event=self._make_publisher(queue))
        graph = build_graph(deps)
        state = TutorState(**(initial or {}))
        session = SessionData(id=sid, state=state, deps=deps, graph=graph, queue=queue, store=self)
        with self._lock:
            self._sessions[sid] = session
        return session

    def _sweep(self, now: Optional[float] = None) -> int:
        """Удаляет сессии, бездействующие дольше TTL. Возвращает число удалённых."""
        now = now or time.monotonic()
        with self._lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if now - s.last_activity > self._ttl
            ]
            for sid in expired:
                self._sessions.pop(sid, None)
        return len(expired)

    @staticmethod
    def _make_publisher(queue):
        def publish(event: str, data: Dict[str, Any]) -> None:
            queue.put(WsEvent(event=event, data=data))
        return publish

    def get(self, session_id: str) -> Optional[SessionData]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_activity = time.monotonic()
            return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def all_ids(self) -> list:
        with self._lock:
            return list(self._sessions.keys())

    def restore_or_create(self, initial: Optional[Dict[str, Any]] = None) -> Optional[SessionData]:
        """Восстановить сессию из SQLite если есть, иначе создать новую."""
        import uuid
        
        self._sweep()
        
        # Ищем сохранённую сессию
        if self._sqlite is not None:
            sid_candidates = self._sqlite.list_ids()
            # Проверяем каждую сохранённую сессию на актуальность
            for candidates_sid in sorted(sid_candidates):
                saved_state = self._sqlite.load(candidates_sid)
                if saved_state and saved_state.get("quiz_complete") is False:
                    if saved_state.get("source_status") == "ready" or saved_state.get("collection_id"):
                        # Восстанавливаем существующую активную сессию
                        queue: "std_queue.Queue[WsEvent]" = std_queue.Queue()
                        deps = replace(self._base_deps, on_event=self._make_publisher(queue))
                        graph = build_graph(deps)  # rebuild для персистентности
                        state = TutorState.model_validate(saved_state)
                        session = SessionData(
                            id=candidates_sid,
                            state=state,
                            deps=deps,
                            graph=graph,
                            queue=queue,
                            store=self,
                        )
                        with self._lock:
                            self._sessions[candidates_sid] = session
                        logger.info("Restored session %s from SQLite (%d records)", candidates_sid, len(state.records))
                        return session
        
        # Нет сохранённой сессии — создаём новую
        sid = uuid.uuid4().hex[:12]
        queue: "std_queue.Queue[WsEvent]" = std_queue.Queue()
        deps = replace(self._base_deps, on_event=self._make_publisher(queue))
        graph = build_graph(deps)
        state = TutorState(**(initial or {}))
        session = SessionData(id=sid, state=state, deps=deps, graph=graph, queue=queue, store=self)
        with self._lock:
            self._sessions[sid] = session
        return session


async def run_step(session: SessionData, answer: Optional[str] = None) -> TutorState:
    """Один шаг графа в worker-потоке (не блокирует event loop).

    Зависший шаг (> RUN_STEP_TIMEOUT_SEC) прерывается таймаутом — сессия не
    блокируется, возвращается сообщение об ошибке.
    """
    from src.config import settings as cfg_settings

    if answer is not None:
        session.state = session.state.model_copy(update={"pending_answer": answer})
    state_dict = session.state.model_dump()

    def _invoke() -> TutorState:
        if session.cancel_event.is_set():
            session.cancel_event.clear()
            return session.state
        try:
            return TutorState.model_validate(session.graph.invoke(state_dict))
        except Exception as e:  # прагматично: ошибка шага не роняет сессию
            logger.exception("run_step: ошибка шага графа: %s", e)
            st = session.state.model_copy(deep=True)
            st.agent_message = f"Ошибка выполнения шага: {e}"
            st.session_status = "failed"
            return st

    timeout = float(getattr(cfg_settings, "RUN_STEP_TIMEOUT_SEC", 300.0))
    try:
        session.state = await asyncio.wait_for(asyncio.to_thread(_invoke), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("run_step: шаг превысил таймаут %ss (сессия %s)", timeout, session.id)
        st = session.state.model_copy(deep=True)
        st.agent_message = (
            "Операция заняла слишком много времени. Попробуйте загрузить файл поменьше "
            "или «Найти учебник»."
        )
        st.session_status = "failed"
        session.state = st
    
    # Сохраняем состояние после каждого шага (персистентность)
    if session.store and session.store._sqlite:
        try:
            session.store._save_state(session.id, session.state)
        except Exception:
            logger.exception("run_step: ошибка сохранения %s", session.id)
    
    return session.state


def intake_status(state: TutorState) -> IntakeStatusResponse:
    """IntakeStatusResponse из состояния (В-4)."""
    missing = list(state.missing_fields) or compute_missing(state)
    return IntakeStatusResponse(
        missing_fields=missing,
        next_question=state.agent_question or "",
        complete=not missing,
    )


def message_response(state: TutorState) -> MessageResponse:
    """MessageResponse из состояния (раздел 8.2)."""
    if state.intake_field:
        return MessageResponse(
            type="intake_question",
            payload={"question": state.agent_question or "", "missing_fields": state.missing_fields},
        )
    if state.quiz_complete or state.session_status == "completed":
        return MessageResponse(
            type="summary",
            payload={
                "summary": state.summary_text or "",
                "knowledge_map": state.knowledge_map,
                "correct": state.correct_count,
                "total": state.answered_count,
            },
        )
    if state.current_question is not None:
        return MessageResponse(type="quiz_card", payload=state.current_question.model_dump())
    if state.session_status == "failed":
        return MessageResponse(type="error", payload={"message": state.agent_message or ""})
    if state.agent_message:
        return MessageResponse(type="explanation", payload={"text": state.agent_message})
    return MessageResponse(type="system", payload={"message": ""})


async def get_next_event(session: SessionData, timeout: float = WS_IDLE_TIMEOUT_SEC) -> Optional[WsEvent]:
    """Чтение события для WS (потокобезопасно, через to_thread)."""
    try:
        event = await asyncio.to_thread(session.queue.get, timeout=timeout)
        return event
    except std_queue.Empty:
        return None
