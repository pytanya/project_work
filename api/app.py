"""
EduTutor — FastAPI application (раздел 8).

Запуск:
    uvicorn api.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import datetime
import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from .engine import SessionStore
from .routes import documents, graph, intake, messages, sessions, source, students, wiki

_LOGGING_INITIALIZED = False


def _setup_prod_logging() -> None:
    """Файловый лог в проде (FastAPI): данные/uvicorn.log + корневой logger.

    main.py (CLI) вызывает setup_logging отдельно; для веб-режима root handler
    не настроен — добавляем FileHandler к корню, не удаляя существующие
    (uvicorn пишет в консоль, мы дублируем в файл для диагностики зависаний).
    """
    global _LOGGING_INITIALIZED
    if _LOGGING_INITIALIZED:
        return
    _LOGGING_INITIALIZED = True
    try:
        from src.config import settings as cfg

        log_path = Path(cfg.LOGS_DIR) / f"uvicorn_{datetime.datetime.now():%Y%m%d}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        logging.getLogger("ddgs").setLevel(logging.WARNING)
        logging.getLogger("primp").setLevel(logging.WARNING)
        root.info("EduTutor logging: %s", log_path)
    except Exception as exc:  # pragma: no cover — логирование не должно ронять приложение
        logging.getLogger("edututor.api").warning("Файловый лог недоступен: %s", exc)


def create_app(store: Optional[SessionStore] = None) -> FastAPI:
    app = FastAPI(title="EduTutor API", version="0.1.0", description="Образовательный агент-тьютор (раздел 8)")
    _setup_prod_logging()
    app.state.store = store or SessionStore()

    app.include_router(sessions.router)
    app.include_router(intake.router)
    app.include_router(documents.router)
    app.include_router(source.router)
    app.include_router(messages.router)
    app.include_router(graph.router)
    app.include_router(wiki.router)
    app.include_router(students.router)

    @app.get("/api/health", tags=["monitoring"])
    def health():
        st = app.state.store
        store = getattr(st, "_base_deps", None).store if getattr(st, "_base_deps", None) else None
        # HybridVectorStore — обёртка над реальным бэкендом; отдаём внутренний класс
        inner = getattr(store, "inner", store)
        backend = type(inner).__name__ if inner else "unknown"
        return {
            "status": "ok",
            "vector_store": backend,
            "collection": getattr(store, "collection_name", None),
            "circuit_breaker": getattr(st, "_circuit", None).to_dict() if getattr(st, "_circuit", None) else None,
        }

    @app.get("/api/metrics", tags=["monitoring"])
    def metrics():
        st = app.state.store
        body = (
            "# HELP edututor_sessions_active Активные сессии\n"
            "# TYPE edututor_sessions_active gauge\n"
            f"edututor_sessions_active {len(st.all_ids())}\n"
        )
        return PlainTextResponse(body, media_type="text/plain")

    return app


app = create_app()
