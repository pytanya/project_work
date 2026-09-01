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
from src.config import ROLE_CHEAP, ROLE_EXPERT, ROLE_JUDGE, ROLE_TUTOR
from src.graph import GraphDeps, build_graph, make_graph_deps
from src.guardrails import BudgetExceededError, BudgetGuard, CircuitBreaker
from src.intake import compute_missing
from src.metrics import MetricsCollector
from src.states import TutorState

logger = logging.getLogger("edututor.api")

# Idle-таймаут WS: должен переживать длинную обработку (парсинг+индексация ~1-2 мин)
WS_IDLE_TIMEOUT_SEC = 300


_LLM_OFFLINE_HINTS = (
    "все провайдеры и модели недоступны",
    "apiconnectionerror",
    "connectionerror",
    "connection refused",
    "getaddrinfo",
    "socket.gaierror",
    "nodename nor servname",
    "name resolution",
    "could not connect",
    "timed out",
    "timeout",
)


def _friendly_step_error(exc: Exception) -> str:
    """Пользовательское сообщение вместо сырой ошибки LLM/сети.

    Офлайн-сценарий (нет wifi / недоступен AI-сервис): показываем понятное
    сообщение с действием, а не «RuntimeError: Все провайдеры и модели недоступны».
    """
    low = str(exc).lower()
    if any(h in low for h in _LLM_OFFLINE_HINTS):
        return (
            "Не удалось получить ответ от AI-сервиса: интернет недоступен или сервис "
            "не отвечает. Подключитесь к сети и повторите. Загруженные материалы и "
            "прогресс сохраняются."
        )
    if isinstance(exc, BudgetExceededError) or "бюджет" in low:
        return (
            "Исчерпан бюджет AI-запросов сессии (лимит стоимости или числа вызовов). "
            "Начните новую сессию или повторите позже."
        )
    return f"Ошибка выполнения шага: {exc}"


def _close_logger(step_logger: Any) -> None:
    """Закрыть JSONL-логгер сессии (безопасно при None/ошибке)."""
    if step_logger is not None:
        try:
            step_logger.close()
        except Exception:
            pass


def _fallback_deps() -> GraphDeps:
    """Портативный fallback при недоступности стандартного хранилища.

    Qdrant embedded-режим — однопоточный (одна сессия на каталог): если сервер
    уже держит data/qdrant, вторая сессия (тесты/другой сервер) падает.
    Fallback — NumpyVectorStore в памяти (без блокировок).
    """
    from src.config import settings as cfg
    from src.knowledge import NumpyVectorStore, make_collection_name, make_embedder

    embedder = make_embedder(cfg)
    store = NumpyVectorStore(make_collection_name(embedder), embedder)
    return GraphDeps(embedder=embedder, store=store, settings=cfg,
                     collection_name=store.collection_name)


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
    step_active: bool = False  # выполняется ли сейчас шаг графа (WS не закрываем по idle во время шага)
    last_activity: float = field(default_factory=time.monotonic)
    store: Optional["SessionStore"] = None  # reference to parent store for persistence
    history_written: tuple = ()  # последняя записанная в историю сводка (прогрессивная запись)
    # Наблюдаемость и лимиты сессии (None — если llm-колбэки заданы извне/тесты)
    metrics: Optional[MetricsCollector] = None
    budget: Optional[BudgetGuard] = None
    step_logger: Any = None  # JsonlStepLogger: JSONL-трассировка запроса


class SessionStore:
    """Хранилище сессий. deps — базовые зависимости (embedder/store переиспользуются).

    Устаревшие сессии (бездействие > SESSION_IDLE_TTL_SEC) удаляются при создании
    новых — сервер не копит мусор.
    
    Опционально: SQLite-прослойка для персистентности состояний.
    """

    def __init__(self, deps: Optional[GraphDeps] = None,
                 sqlite_store: Any = None, ttl: Optional[float] = None):
        from src.session_store import SessionSQLiteStore

        if deps is None:
            try:
                deps = make_graph_deps()
            except Exception as e:
                # Qdrant embedded залочен другой сессией / сервер недоступен —
                # не роняем бэкенд, используем портативный numpy-стор.
                logger.warning("Стандартные deps недоступны (%s) — fallback на numpy store", e)
                deps = _fallback_deps()
        self._base_deps = deps or _fallback_deps()
        self._sessions: Dict[str, SessionData] = {}
        self._lock = threading.Lock()
        base_settings = getattr(self._base_deps, "settings", None)
        self._ttl = ttl if ttl is not None else float(getattr(base_settings, "SESSION_IDLE_TTL_SEC", 1800.0))
        
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

        # Circuit breaker (guardrails.py): при N сбоях шагов подряд — fail closed
        base_settings = getattr(self._base_deps, "settings", None)
        self._circuit = CircuitBreaker(
            failure_threshold=int(getattr(base_settings, "CIRCUIT_BREAKER_THRESHOLD", 3) or 3),
            cooldown_seconds=float(getattr(base_settings, "CIRCUIT_BREAKER_COOLDOWN_SEC", 30.0) or 30.0),
        )

        # Персистентные профили учеников (студенты) — персональные Wiki/мастерство/заметки
        from src.student import StudentStore

        self.student_store = StudentStore(getattr(base_settings, "STUDENTS_DIR", None) if base_settings else None)

    def _save_state(self, sid: str, st: TutorState) -> None:
        """Сохранить состояние в SQLite после каждого шага."""
        if self._sqlite is not None:
            try:
                self._sqlite.save(sid, st.model_dump())
            except Exception:
                logger.exception("Ошибка сохранения %s", sid)

    def _make_step_logger(self, sid: str) -> Any:
        """JSONL-логгер сессии: request_id = req_<sid>, файл logs/session_<sid>.jsonl."""
        try:
            from src.config import settings as cfg
            from src.logging_setup import JsonlStepLogger

            logs_dir = cfg.LOGS_DIR
            logs_dir.mkdir(parents=True, exist_ok=True)
            return JsonlStepLogger(logs_dir / f"session_{sid}.jsonl",
                                   request_id=f"req_{sid}", session_id=sid)
        except Exception:
            logger.warning("Не удалось создать JSONL-логгер сессии %s", sid)
            return None

    def _session_deps(self, queue: "std_queue.Queue[WsEvent]"):
        """deps для новой сессии: publisher/стриминг + live-LLM (метрики/бюджет).

        Возвращает (deps, metrics, budget). metrics/budget = None, если llm-колбэки
        заданы извне (тесты/инъекция) — тогда лимиты не контролируются.
        """
        deps = replace(
            self._base_deps,
            on_event=self._make_publisher(queue),
            on_token=self._make_token_publisher(queue),
        )
        if getattr(deps, "tutor_llm", None) is not None:
            return deps, None, None
        return self._wire_live_llm(deps)

    def _wire_live_llm(self, deps: GraphDeps):
        """Production: привязать LLM-клиенты к метрикам и бюджету сессии.

        Все llm-колбэки пусты (make_graph_deps) → создаём клиентов с metrics/budget,
        включая agent_llm (function calling). При недоступности провайдеров оставляем
        deps как есть — ошибка всплывёт с понятным сообщением на первом шаге.
        """
        from src.llm_client import LLMClient

        metrics = MetricsCollector()
        budget = BudgetGuard(deps.settings)
        try:
            tutor = LLMClient(role=ROLE_TUTOR, metrics=metrics, budget=budget)
            cheap = LLMClient(role=ROLE_CHEAP, metrics=metrics, budget=budget)
            expert = LLMClient(role=ROLE_EXPERT, metrics=metrics, budget=budget)
            judge = LLMClient(role=ROLE_JUDGE, metrics=metrics, budget=budget)
            agent_tutor = LLMClient(role=ROLE_TUTOR, metrics=metrics, budget=budget)
        except Exception as exc:  # нет провайдеров (пустой .env) — не роняем бэкенд
            logger.warning("Live-LLM недоступны (%s) — deps без бюджетного контроля", exc)
            return deps, None, None
        deps = replace(
            deps,
            tutor_llm=lambda m: tutor.chat(m, temperature=0.3, max_tokens=512).content or "",
            eval_llm=lambda m: cheap.chat(m, temperature=0.0, max_tokens=300).content or "",
            expert_llm=lambda m: expert.chat(m, temperature=0.2, max_tokens=500).content or "",
            judge_llm=lambda m: judge.chat(m, temperature=0.0, max_tokens=200).content or "",
            agent_llm=lambda msgs, tools=None: agent_tutor.chat(msgs, tools=tools, max_tokens=500, temperature=0.2),
        )
        return deps, metrics, budget

    def _bind_session(self, deps: GraphDeps, step_logger: Any) -> GraphDeps:
        """Прикрепить JSONL-логгер к deps сессии (не мутируя базовые deps)."""
        deps = replace(deps)
        deps.step_logger = step_logger
        return deps

    def create(self, initial: Optional[Dict[str, Any]] = None) -> SessionData:
        self._sweep()
        sid = uuid.uuid4().hex[:12]
        queue: "std_queue.Queue[WsEvent]" = std_queue.Queue()
        deps, metrics, budget = self._session_deps(queue)
        step_logger = self._make_step_logger(sid)
        deps = self._bind_session(deps, step_logger)
        graph = build_graph(deps)
        state = TutorState(**(initial or {}))
        session = SessionData(id=sid, state=state, deps=deps, graph=graph, queue=queue, store=self,
                              metrics=metrics, budget=budget, step_logger=step_logger)
        with self._lock:
            self._sessions[sid] = session
        return session

    def _sweep(self, now: Optional[float] = None) -> int:
        """Удаляет сессии, бездействующие дольше TTL. Возвращает число удалённых."""
        now = now or time.monotonic()
        with self._lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if now - s.last_activity > self._ttl and not s.step_active
            ]
            for sid in expired:
                s = self._sessions.pop(sid, None)
                if s is not None:
                    _close_logger(s.step_logger)
        return len(expired)

    @staticmethod
    def _make_publisher(queue):
        def publish(event: str, data: Dict[str, Any]) -> None:
            queue.put(WsEvent(event=event, data=data))
        return publish

    @staticmethod
    def _make_token_publisher(queue):
        """Реальный стриминг токенов (stream=True) → WS-событие `token`."""
        def publish_token(text: str) -> None:
            queue.put(WsEvent(event="token", data={"text": text}))
        return publish_token

    def get(self, session_id: str) -> Optional[SessionData]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_activity = time.monotonic()
            return session

    def apply_source_policy_to_sessions(self, student_id: str, allow_any: bool, whitelist: List[str]) -> int:
        """Применить политику источников к активным сессиям ученика.

        Возвращает число обновлённых сессий. Следующий поиск уже пойдёт по новой политике.
        """
        updated = 0
        with self._lock:
            for sid, s in self._sessions.items():
                st = getattr(s, "state", None)
                if st is None or getattr(st, "student_id", None) != student_id:
                    continue
                try:
                    s.state = st.model_copy(update={
                        "allow_any_sources": bool(allow_any),
                        "source_whitelist": list(whitelist),
                    })
                    updated += 1
                except Exception:
                    logger.exception("apply_source_policy: сессия %s", sid)
        return updated

    def delete(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            _close_logger(session.step_logger)
            self._log_session_history(session)
        return session is not None

    def _history_entry(self, session: SessionData) -> Optional[Dict[str, Any]]:
        """Сводка сессии для истории занятий (или None, если писать нечего)."""
        st = session.state
        if not st or not getattr(st, "student_id", None):
            return None
        if not (st.subject or st.topic or st.answered_count or st.lesson_done):
            return None  # пустой сеанс (только создали и закрыли) — не пишем
        return {
            "session_id": session.id,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "subject": st.subject or "",
            "topic": st.topic or "",
            "mode": st.mode or "",
            "lesson_done": bool(st.lesson_done),
            "correct": st.correct_count or 0,
            "answered": st.answered_count or 0,
            "total": st.num_questions or st.answered_count or 0,
        }

    def _log_session_history(self, session: SessionData) -> None:
        """Сводка закрытой сессии → история занятий ученика (студенты/sessions/)."""
        try:
            entry = self._history_entry(session)
            if entry is not None:
                self.student_store.log_session(session.state.student_id, entry)
        except Exception:
            logger.exception("log_session_history: не удалось сохранить сводку")

    def _maybe_log_session_history(self, session: SessionData) -> None:
        """Прогрессивная запись истории: после урока и после квиза, без дублей.

        Вызывается после каждого шага графа; пишет только когда изменился
        значимый снимок (урок показан / отвечено вопросов / квиз завершён).
        """
        st = session.state
        if not st or not getattr(st, "student_id", None):
            if st and not getattr(st, "student_id", None):
                logger.debug(
                    "_maybe_log_session_history: student_id не задан в сессии %s — "
                    "история не будет записана. Проверьте, что профиль ученика "
                    "создаётся при создании сессии (StudentProfile.prefill).",
                    session.id,
                )
            return
        snapshot = (bool(st.lesson_done), st.answered_count or 0, bool(st.quiz_complete))
        if snapshot == session.history_written:
            return
        entry = self._history_entry(session)
        if entry is None:
            return
        try:
            self.student_store.log_session(st.student_id, entry)
            session.history_written = snapshot
        except Exception:
            logger.exception("_maybe_log_session_history: не удалось сохранить сводку")

    def all_ids(self) -> list:
        with self._lock:
            return list(self._sessions.keys())

    def check_student_access(self, student_id: str) -> bool:
        """Проверить, есть ли активная или недавняя сессия для данного student_id.

        Используется как базовая защита от подмены student_id в публичных API.
        Если студент не имеет ни одной сессии — доступ запрещён (возвращает пустые данные).
        """
        with self._lock:
            for sid, s in self._sessions.items():
                st = getattr(s, "state", None)
                if st and getattr(st, "student_id", None) == student_id:
                    return True
        return False

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
                        deps, metrics, budget = self._session_deps(queue)
                        step_logger = self._make_step_logger(candidates_sid)
                        deps = self._bind_session(deps, step_logger)
                        graph = build_graph(deps)  # rebuild для персистентности
                        state = TutorState.model_validate(saved_state)
                        session = SessionData(
                            id=candidates_sid,
                            state=state,
                            deps=deps,
                            graph=graph,
                            queue=queue,
                            store=self,
                            metrics=metrics,
                            budget=budget,
                            step_logger=step_logger,
                        )
                        with self._lock:
                            self._sessions[candidates_sid] = session
                        logger.info("Restored session %s from SQLite (%d records)", candidates_sid, len(state.records))
                        return session
        
        # Нет сохранённой сессии — создаём новую
        sid = uuid.uuid4().hex[:12]
        queue: "std_queue.Queue[WsEvent]" = std_queue.Queue()
        deps, metrics, budget = self._session_deps(queue)
        step_logger = self._make_step_logger(sid)
        deps = self._bind_session(deps, step_logger)
        graph = build_graph(deps)
        state = TutorState(**(initial or {}))
        session = SessionData(id=sid, state=state, deps=deps, graph=graph, queue=queue, store=self,
                              metrics=metrics, budget=budget, step_logger=step_logger)
        with self._lock:
            self._sessions[sid] = session
        return session


async def run_step(session: SessionData, answer: Optional[str] = None) -> TutorState:
    """Один шаг графа в worker-потоке (не блокирует event loop).

    Зависший шаг (> RUN_STEP_TIMEOUT_SEC) прерывается таймаутом — сессия не
    блокируется, возвращается сообщение об ошибке.
    Circuit breaker (guardrails.py): при серии сбоев — fail closed на cooldown.
    """
    from src.config import settings as cfg_settings

    store = session.store
    circuit = getattr(store, "_circuit", None) if store is not None else None
    sl = getattr(session.deps, "step_logger", None)

    # Circuit breaker: защитная пауза, если сервис нестабилен (N сбоев подряд)
    if circuit is not None and circuit.is_open():
        logger.warning("run_step: circuit breaker open (сессия %s) — fail closed", session.id)
        st = session.state.model_copy(deep=True)
        st.agent_message = (
            "AI-сервис временно недоступен — автоматическая защитная пауза. "
            "Повторите через минуту."
        )
        st.session_status = "failed"
        session.state = st
        return st

    if answer is not None:
        session.state = session.state.model_copy(update={"pending_answer": answer})
    state_dict = session.state.model_dump()

    if sl is not None:
        try:
            sl.log_step(agent_action="user_request", status="start",
                        extra={"text": (answer or "")[:200]})
        except Exception:
            pass

    def _invoke():
        if session.cancel_event.is_set():
            session.cancel_event.clear()
            return session.state, None
        try:
            return TutorState.model_validate(session.graph.invoke(state_dict)), None
        except Exception as e:  # прагматично: ошибка шага не роняет сессию
            logger.exception("run_step: ошибка шага графа: %s", e)
            st = session.state.model_copy(deep=True)
            st.agent_message = _friendly_step_error(e)
            st.session_status = "failed"
            return st, e

    timeout = float(getattr(cfg_settings, "RUN_STEP_TIMEOUT_SEC", 300.0))
    session.step_active = True

    # Heartbeat (оптимизация #5): каждые 15 сек шлём событие, чтобы фронтенд знал,
    # что долгая генерация ещё идёт, и продлевал свой busy-таймаут.
    async def _heartbeat():
        start = time.monotonic()
        while session.step_active:
            await asyncio.sleep(15)
            if session.step_active:
                elapsed = int(time.monotonic() - start)
                session.queue.put(WsEvent(
                    event="system.heartbeat",
                    data={"message": "Обработка продолжается…", "elapsed": elapsed},
                ))

    hb_task = asyncio.create_task(_heartbeat())
    step_exc: Optional[Exception] = None
    try:
        result, step_exc = await asyncio.wait_for(asyncio.to_thread(_invoke), timeout=timeout)
        session.state = result
    except asyncio.TimeoutError:
        logger.error("run_step: шаг превысил таймаут %ss (сессия %s)", timeout, session.id)
        st = session.state.model_copy(deep=True)
        st.agent_message = (
            "Операция заняла слишком много времени. Попробуйте загрузить файл поменьше "
            "или «Найти учебник»."
        )
        st.session_status = "failed"
        session.state = st
        step_exc = asyncio.TimeoutError()
    finally:
        session.step_active = False
        hb_task.cancel()

    if circuit is not None:
        if step_exc is not None:
            circuit.record_failure()
        else:
            circuit.record_success()

    if sl is not None:
        try:
            sl.log_step(agent_action="user_request", status="end",
                        extra={"session_status": session.state.session_status or ""})
        except Exception:
            pass

    # Сохраняем состояние после каждого шага (персистентность)
    if session.store and session.store._sqlite:
        try:
            session.store._save_state(session.id, session.state)
        except Exception:
            logger.exception("run_step: ошибка сохранения %s", session.id)

    # Прогрессивная история занятий: урок показан / квиз пройден — запись без дублей
    if session.store is not None:
        try:
            session.store._maybe_log_session_history(session)
        except Exception:
            logger.exception("run_step: прогрессивная история %s", session.id)

    # Судья урока теперь ТОЛЬКО по запросу (кнопка «Оценить урок» → POST /judge).
    # Авто-запуск убран: фоновый LLM-судья не дёргается на каждом шаге (наблюдаемость
    # показала: при недоступном провайдере это добавляло секунды к шагу).

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
    if state.lesson_text is not None and not state.lesson_confirmed:
        # 7.3.3: урок/разбор не теряется при HTTP-пути и resync
        return MessageResponse(
            type="lesson",
            payload=state.lesson_payload(topic=state.active_topic or state.topic or ""),
        )
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
