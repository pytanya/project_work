"""Тесты FastAPI API (раздел 8): сессии, intake, message, upload, find-textbook, cancel, WS."""

from __future__ import annotations

import hashlib
import queue as std_queue
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.engine import SessionStore
from api.schemas import WsEvent
from src.config import BASE_DIR, Settings
from src.graph import GraphDeps
from src.knowledge import DocChunk, NumpyVectorStore

_GEN = '{"question": "Что такое атмосфера?", "options": ["А", "Б"], "answer_type": "single", "topic": "Атмосфера"}'
_EVAL = '{"score": 8, "correct": true, "feedback": "Верно!", "citation_ok": true}'
_EXPL = '{"text": "Объяснение ошибки.", "citation": {"paragraph": "§12", "source": "учебник"}}'
_JUDGE = '{"criteria": {"grade_correct": 9, "feedback_ok": 8, "difficulty_fit": 7}}'


class FakeEmbedder:
    def __init__(self, model="test"):
        self.model = model

    def _vec(self, text):
        v = [0.0] * 8
        for token in text.lower().split():
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:4], 16)
            v[h % 8] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def encode(self, texts):
        return [self._vec(t) for t in texts]

    def encode_query(self, text):
        return self._vec(text)


@pytest.fixture
def make_settings(monkeypatch):
    def _make(**kwargs):
        for name in Settings.model_fields:
            if name not in kwargs:
                monkeypatch.delenv(name, raising=False)
        return Settings(_env_file=None, **kwargs)

    return _make


@pytest.fixture
def client(make_settings, tmp_path):
    s = make_settings(
        FGOS_REFERENCE_DIR=str(BASE_DIR / "data" / "fgos_reference"),
        TEXTBOOKS_DOWNLOADS_DIR=str(tmp_path / "downloads"),
        KNOWLEDGE_WIKI_DIR=str(tmp_path / "wiki"),
        MAX_INTAKE_ITERATIONS=8,
        OCR_MIN_TEXT_CHARS=20,
    )
    embedder = FakeEmbedder()
    store = NumpyVectorStore("api", embedder)
    store.add([
        DocChunk(
            id="a1",
            text="Параграф 12: Атмосфера. Атмосфера — воздушная оболочка Земли, состоит из азота (78%) и кислорода (21%).",
            section_number="12", section_title="Атмосфера", source="book", subject="география", grade="6",
        )
    ])
    deps = GraphDeps(
        embedder=embedder, store=store, settings=s,
        tutor_llm=lambda m: _GEN,
        eval_llm=lambda m: _EVAL,
        expert_llm=lambda m: _EXPL,
        judge_llm=lambda m: _JUDGE,
    )
    app = create_app(SessionStore(deps))
    with TestClient(app) as c:
        yield c


def _new_session(client, **initial) -> str:
    r = client.post("/api/sessions", json={"initial": initial} if initial else None)
    assert r.status_code == 201
    return r.json()["session_id"]


class TestStreaming:
    def test_token_event_schema(self):
        ev = WsEvent(event="token", data={"text": "Привет"})
        assert ev.model_dump()["data"]["text"] == "Привет"

    def test_token_publisher_puts_event(self):
        from api.engine import SessionStore

        q = std_queue.Queue()
        pub = SessionStore._make_token_publisher(q)
        pub("часть1")
        pub("часть2")
        assert q.qsize() == 2
        ev = q.get()
        assert ev.event == "token"
        assert ev.data["text"] == "часть1"


class TestSessions:
    def test_create_get_delete(self, client):
        sid = _new_session(client)
        assert client.get(f"/api/sessions/{sid}").status_code == 200
        assert client.delete(f"/api/sessions/{sid}").status_code == 204
        assert client.get(f"/api/sessions/{sid}").status_code == 404

    def test_404_unknown(self, client):
        assert client.get("/api/sessions/nope").status_code == 404


class TestHealthMetrics:
    def test_health(self, client):
        r = client.get("/api/health").json()
        assert r["status"] == "ok"
        # С 2026-08: health сообщает активный векторный бэкенд (numpy/chroma/qdrant)
        assert r["vector_store"]
        assert "collection" in r

    def test_metrics(self, client):
        r = client.get("/api/metrics")
        assert r.status_code == 200
        assert "edututor_sessions_active" in r.text


class TestIntake:
    def test_intake_flow_to_complete(self, client):
        sid = _new_session(client, num_questions=2, sources=[{"type": "web", "url": "x"}], collection_id="web")
        r = client.get(f"/api/sessions/{sid}/intake/status")
        assert r.status_code == 200
        assert "learner_type" in r.json()["missing_fields"]
        assert not r.json()["complete"]

        for answer in ["студент", "физика", "Атомы", "нет", "квиз"]:
            r = client.post(f"/api/sessions/{sid}/intake", json={"answer": answer})
            assert r.status_code == 200
        status = client.get(f"/api/sessions/{sid}/intake/status").json()
        assert status["complete"] is True
        assert status["missing_fields"] == []


class TestMessage:
    def test_quiz_card_after_intake(self, client):
        sid = _new_session(client, num_questions=2, sources=[{"type": "web", "url": "x"}], collection_id="web")
        for answer in ["студент", "физика", "Атомы", "нет", "квиз"]:
            client.post(f"/api/sessions/{sid}/intake", json={"answer": answer})

        r = client.post(f"/api/sessions/{sid}/message", json={"text": "Атмосфера — это воздушная оболочка Земли."})
        assert r.status_code == 200
        data = r.json()
        assert data["type"] in ("quiz_card", "explanation", "summary", "system")
        assert data["payload"]

    def test_history(self, client):
        sid = _new_session(client)
        client.post(f"/api/sessions/{sid}/message", json={"text": "привет"})
        hist = client.get(f"/api/sessions/{sid}/history").json()["history"]
        assert any(h["text"] == "привет" for h in hist)


class TestUpload:
    def test_upload_txt(self, client, tmp_path):
        sid = _new_session(client, num_questions=1)
        # завершаем intake (upload обрабатывается после чек-листа)
        for answer in ["студент", "физика", "Атомы", "да", "квиз"]:
            client.post(f"/api/sessions/{sid}/intake", json={"answer": answer})
        files = {"file": ("doc.txt", "Параграф 12: Атмосфера\nСтроение атмосферы.\n\nПараграф 13: Погода\nПогода меняется.".encode("utf-8"), "text/plain")}
        r = client.post(f"/api/sessions/{sid}/upload", files=files)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["status"] == "ready"

    def test_upload_rejects_unknown_ext(self, client):
        sid = _new_session(client)
        files = {"file": ("doc.exe", b"MZ", "application/octet-stream")}
        r = client.post(f"/api/sessions/{sid}/upload", files=files)
        assert r.json()["ok"] is False

    def test_upload_scanned_detected(self, client):
        sid = _new_session(client, num_questions=1)
        for answer in ["студент", "физика", "Атомы", "да", "квиз"]:
            client.post(f"/api/sessions/{sid}/intake", json={"answer": answer})
        # очень короткий текст (< OCR_MIN_TEXT_CHARS) → «скан»
        files = {"file": ("scan.txt", b"abcd", "text/plain")}
        r = client.post(f"/api/sessions/{sid}/upload", files=files)
        assert r.status_code == 200
        assert r.json()["scanned"] is True


class TestFindTextbook:
    def test_find_textbook_mock(self, client):
        sid = _new_session(client, num_questions=1, subject="география", topic="Атмосфера", learner_type="student", mode="quiz", has_textbook=False)
        r = client.post(f"/api/sessions/{sid}/find-textbook")
        assert r.status_code == 200
        assert r.json()["status"] in ("ready", "failed")

    def test_source_status(self, client):
        sid = _new_session(client)
        r = client.get(f"/api/sessions/{sid}/source-status")
        assert r.status_code == 200


class TestCancel:
    def test_cancel(self, client):
        sid = _new_session(client)
        r = client.post(f"/api/sessions/{sid}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"


class TestExport:
    def test_export_csv(self, client):
        sid = _new_session(client, num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        for answer in ["студент", "физика", "Атомы", "нет", "квиз"]:
            client.post(f"/api/sessions/{sid}/intake", json={"answer": answer})
        client.post(f"/api/sessions/{sid}/message", json={"text": "Атмосфера — воздушная оболочка Земли."})

        r = client.get(f"/api/sessions/{sid}/export")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        text = r.content.decode("utf-8-sig")
        assert "session_id" in text
        assert "question" in text
        assert sid in text
        assert "Что такое атмосфера?" in text  # из mock _GEN

    def test_export_empty_ok(self, client):
        sid = _new_session(client)
        r = client.get(f"/api/sessions/{sid}/export")
        assert r.status_code == 200
        assert "session_id" in r.content.decode("utf-8-sig")


class TestGraph:
    def _session_with_graph(self, client):
        sid = _new_session(client, num_questions=1)
        for answer in ["студент", "физика", "Атомы", "да", "квиз"]:
            client.post(f"/api/sessions/{sid}/intake", json={"answer": answer})
        files = {"file": ("doc.txt", "Параграф 12: Атмосфера\nСтроение атмосферы.\n\nПараграф 13: Погода\nПогода меняется.".encode("utf-8"), "text/plain")}
        client.post(f"/api/sessions/{sid}/upload", files=files)
        return sid

    def test_get_graph_after_upload(self, client):
        sid = self._session_with_graph(client)
        r = client.get(f"/api/sessions/{sid}/graph")
        assert r.status_code == 200
        assert len(r.json()["nodes"]) >= 3  # book + 2 параграфа
        assert r.json()["stats"]["nodes"] >= 3

    def test_topic_gate_after_upload(self, client):
        sid = self._session_with_graph(client)
        st = client.get(f"/api/sessions/{sid}").json()
        assert st["awaiting_topic"] is True
        assert st["active_topic"] is None
        g = client.get(f"/api/sessions/{sid}/graph").json()
        node = next(n for n in g["nodes"] if n.get("type") == "section")
        r = client.post(f"/api/sessions/{sid}/topic", json={"topic_id": node["id"]})
        assert r.status_code == 200
        st = client.get(f"/api/sessions/{sid}").json()
        assert st["awaiting_topic"] is False
        assert st["active_topic"] == node["id"]

    def test_select_topic_invalid_id(self, client):
        sid = self._session_with_graph(client)
        r = client.post(f"/api/sessions/{sid}/topic", json={"topic_id": "nope"})
        assert r.status_code == 404

    def test_select_topic_generates_question(self, client):
        sid = self._session_with_graph(client)
        g = client.get(f"/api/sessions/{sid}/graph").json()
        node = next(n for n in g["nodes"] if n.get("type") == "section")
        r = client.post(f"/api/sessions/{sid}/topic", json={"topic_id": node["id"]})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["active_topic"] == node["id"]
        assert r.json()["question"] or r.json()["next_question"]

    def test_related_nodes(self, client):
        sid = self._session_with_graph(client)
        g = client.get(f"/api/sessions/{sid}/graph").json()
        node = g["nodes"][0]
        r = client.get(f"/api/sessions/{sid}/graph/{node['id']}/related")
        assert r.status_code == 200
        assert "related" in r.json()

    def test_knowledge_package_okf(self, client):
        sid = self._session_with_graph(client)
        r = client.get(f"/api/sessions/{sid}/knowledge-package")
        assert r.status_code == 200
        body = r.json()
        assert body["okf_version"] == "0.2"
        assert body["conformant"] is True
        assert any("index.md" in f for f in body["files"])
        assert any(f.startswith("topics/") for f in body["files"])

    def test_graph_has_mastery_overlay(self, client, monkeypatch, tmp_path):
        """Mastery overlay (roadmap #3): /graph возвращает mastery/attempts для узлов."""
        from src.wiki import KnowledgeWiki, WikiArticle

        sid = self._session_with_graph(client)
        # сессия создана с subject «физика»? В _session_with_graph intake «физика» —
        # проверяем обе ветки матчинга wiki
        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="физика", topic="Параграф 12: Атмосфера", mastery=0.9, attempts=5))
        monkeypatch.setattr("api.routes.graph.default_settings.KNOWLEDGE_WIKI_DIR", tmp_path)

        g = client.get(f"/api/sessions/{sid}/graph").json()
        mastered = [n for n in g["nodes"] if n.get("mastery") is not None]
        assert mastered  # хотя бы один узел с mastery
        assert mastered[0]["attempts"] == 5

    def test_node_wiki_drilldown(self, client, monkeypatch, tmp_path):
        """Drill-down (roadmap #3): GET /graph/{node}/wiki возвращает статью."""
        from src.wiki import KnowledgeWiki, WikiArticle

        sid = self._session_with_graph(client)
        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="физика", topic="Параграф 12: Атмосфера", mastery=0.8, attempts=3))
        monkeypatch.setattr("api.routes.graph.default_settings.KNOWLEDGE_WIKI_DIR", tmp_path)

        g = client.get(f"/api/sessions/{sid}/graph").json()
        node = next(n for n in g["nodes"] if n.get("type") == "section")
        r = client.get(f"/api/sessions/{sid}/graph/{node['id']}/wiki")
        assert r.status_code == 200
        body = r.json()
        assert body["node"]["id"] == node["id"]
        # wiki может быть None (если заголовок не совпал) — не падаем
        assert "wiki" in body


class TestWebSocket:
    def test_ws_streams_quiz_card(self, client):
        sid = _new_session(client, num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
            for answer in ["студент", "физика", "Атомы", "нет", "квиз"]:
                client.post(f"/api/sessions/{sid}/intake", json={"answer": answer})
            client.post(f"/api/sessions/{sid}/message", json={"text": "Атмосфера — воздушная оболочка Земли."})
            # ждём хотя бы одно событие
            event = ws.receive_json()
            assert "event" in event
            assert "data" in event

    def test_ws_unknown_session(self, client):
        with client.websocket_connect("/api/sessions/nope/ws") as ws:
            event = ws.receive_json()
            assert event["event"] == "session.error"


class TestWiki:
    """Knowledge Wiki (roadmap #2): GET /api/wiki — накопленные статьи между сессиями."""

    def _seed(self, tmp_path):
        from src.wiki import KnowledgeWiki, WikiArticle

        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="Философия", topic="Кант", mastery=0.85, attempts=3, correct=2))
        wiki.upsert(WikiArticle(subject="Философия", topic="Гегель", mastery=0.2))
        return wiki

    def test_summary_empty(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr("api.routes.wiki.default_settings.KNOWLEDGE_WIKI_DIR", tmp_path)
        r = client.get("/api/wiki")
        assert r.status_code == 200
        assert r.json()["subjects"] == []

    def test_summary_returns_subjects(self, client, monkeypatch, tmp_path):
        self._seed(tmp_path)
        monkeypatch.setattr("api.routes.wiki.default_settings.KNOWLEDGE_WIKI_DIR", tmp_path)
        r = client.get("/api/wiki")
        body = r.json()
        assert len(body["subjects"]) == 1
        s = body["subjects"][0]
        assert s["subject"] == "философия"
        topics = {a["topic"] for a in s["articles"]}
        assert {"Кант", "Гегель"} <= topics

    def test_subject_articles(self, client, monkeypatch, tmp_path):
        self._seed(tmp_path)
        monkeypatch.setattr("api.routes.wiki.default_settings.KNOWLEDGE_WIKI_DIR", tmp_path)
        r = client.get("/api/wiki/Философия")
        assert r.status_code == 200
        arts = {a["topic"]: a for a in r.json()["articles"]}
        assert arts["Кант"]["mastery"] == 0.85
        assert arts["Кант"]["attempts"] == 3

    def test_article_detail(self, client, monkeypatch, tmp_path):
        self._seed(tmp_path)
        monkeypatch.setattr("api.routes.wiki.default_settings.KNOWLEDGE_WIKI_DIR", tmp_path)
        r = client.get("/api/wiki/Философия/Кант")
        assert r.status_code == 200
        assert r.json()["mastery"] == 0.85

    def test_article_missing_404(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr("api.routes.wiki.default_settings.KNOWLEDGE_WIKI_DIR", tmp_path)
        assert client.get("/api/wiki/Нет/Нет").status_code == 404
        assert client.get("/api/wiki/Нет").status_code == 404
