"""Тесты FastAPI API (раздел 8): сессии, intake, message, upload, find-textbook, cancel, WS."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.engine import SessionStore
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
        assert client.get("/api/health").json() == {"status": "ok"}

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

        for answer in ["студент", "физика", "нет", "квиз"]:
            r = client.post(f"/api/sessions/{sid}/intake", json={"answer": answer})
            assert r.status_code == 200
        status = client.get(f"/api/sessions/{sid}/intake/status").json()
        assert status["complete"] is True
        assert status["missing_fields"] == []


class TestMessage:
    def test_quiz_card_after_intake(self, client):
        sid = _new_session(client, num_questions=2, sources=[{"type": "web", "url": "x"}], collection_id="web")
        for answer in ["студент", "физика", "нет", "квиз"]:
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
        for answer in ["студент", "физика", "да", "квиз"]:
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
        for answer in ["студент", "физика", "да", "квиз"]:
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
        for answer in ["студент", "физика", "нет", "квиз"]:
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


class TestWebSocket:
    def test_ws_streams_quiz_card(self, client):
        sid = _new_session(client, num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
            for answer in ["студент", "физика", "нет", "квиз"]:
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
