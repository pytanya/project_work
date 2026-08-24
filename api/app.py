"""
EduTutor — FastAPI application (раздел 8).

Запуск:
    uvicorn api.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from .engine import SessionStore
from .routes import documents, graph, intake, messages, sessions, source, wiki


def create_app(store: Optional[SessionStore] = None) -> FastAPI:
    app = FastAPI(title="EduTutor API", version="0.1.0", description="Образовательный агент-тьютор (раздел 8)")
    app.state.store = store or SessionStore()

    app.include_router(sessions.router)
    app.include_router(intake.router)
    app.include_router(documents.router)
    app.include_router(source.router)
    app.include_router(messages.router)
    app.include_router(graph.router)
    app.include_router(wiki.router)

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
