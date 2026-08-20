"""Регрессионный тест: POST /api/sessions/{id}/topic не должен падать с 500.

История: ошибка `NameError: name 'logger' is not defined` в api/routes/graph.py
вызывала HTTP 500 при выборе темы. Тест проверяет корректный ответ.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import BASE_DIR, Settings
from src.graph import GraphDeps, build_graph
from src.knowledge import NumpyVectorStore
from src.states import TutorState


class FakeEmbedder:
    def __init__(self, model="test"):
        self.model = model

    def _vec(self, text):
        return [1.0, 0.0, 0.0, 0.0]

    def encode(self, texts):
        return [self._vec(t) for t in texts]

    def encode_query(self, text):
        return self._vec(text)


def _make_deps():
    s = Settings(_env_file=None, FGOS_REFERENCE_DIR=str(BASE_DIR / "data" / "fgos_reference"))
    embedder = FakeEmbedder()
    store = NumpyVectorStore("t", embedder)
    return GraphDeps(
        embedder=embedder, store=store, settings=s,
        tutor_llm=lambda m: '{"question":"Вопрос?","options":["А","Б"],"answer_type":"single","topic":"Тема","correct_answers":["Б"]}',
        eval_llm=lambda m: '{"score":8,"correct":true,"feedback":"Верно","citation_ok":true}',
        expert_llm=lambda m: '{"text":"Объяснение","citation":{"paragraph":"§1","source":"book"}}',
        judge_llm=lambda m: '{"criteria":{"grade_correct":9,"feedback_ok":8,"difficulty_fit":7}}',
    )


class FakeStore:
    """API-хранилище с ручным знанием графа."""

    def __init__(self):
        self.deps = _make_deps()
        self._sessions = {}

    def create(self, initial=None):
        from api.engine import SessionData
        from dataclasses import replace
        import queue
        import uuid

        sid = uuid.uuid4().hex[:12]
        q = queue.Queue()
        deps = replace(self.deps, on_event=lambda e, d: q.put({"event": e, "data": d}))
        graph = build_graph(deps)
        state = TutorState(**(initial or {}))
        state.knowledge_graph = {
            "nodes": [
                {"id": "n1", "title": "Урок 1: Атмосфера", "type": "section", "section_number": "1"},
            ],
            "edges": [],
        }
        session = SessionData(id=sid, state=state, deps=deps, graph=graph, queue=q)
        self._sessions[sid] = session
        return session

    def get(self, sid):
        return self._sessions.get(sid)

    def delete(self, sid):
        return self._sessions.pop(sid, None) is not None

    def all_ids(self):
        return list(self._sessions.keys())


def test_select_topic_valid_returns_200():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    store = FakeStore()
    session = store.create()

    app = FastAPI()
    app.state.store = store
    from api.routes.graph import router
    app.include_router(router)

    client = TestClient(app)
    resp = client.post(f"/api/sessions/{session.id}/topic", json={"topic_id": "n1"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json().get("ok") is True


def test_select_topic_invalid_returns_404_not_500():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    store = FakeStore()
    session = store.create()

    app = FastAPI()
    app.state.store = store
    from api.routes.graph import router
    app.include_router(router)

    client = TestClient(app)
    resp = client.post(f"/api/sessions/{session.id}/topic", json={"topic_id": "nonexistent"})
    # Важно: НЕ 500! 404 - корректный ответ
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_select_topic_rejects_double_click_with_409():
    """Fix #1 (race condition): пока шаг графа активен, повторный POST /topic → 409."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    store = FakeStore()
    session = store.create()

    app = FastAPI()
    app.state.store = store
    from api.routes.graph import router
    app.include_router(router)

    client = TestClient(app)
    # Имитируем уже идущий фоновый шаг графа
    session.step_active = True
    resp = client.post(f"/api/sessions/{session.id}/topic", json={"topic_id": "n1"})
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
    # Сессия не должна быть «загрязнена» повторным выбором
    assert "error" in resp.json().get("detail", "").lower() or resp.json().get("detail")
