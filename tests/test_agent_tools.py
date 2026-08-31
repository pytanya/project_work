"""Тесты инструментов agent_loop (спека 2.3, 7.3): детерминированный функционал как tools."""

from __future__ import annotations

import hashlib
import json
import sys

from src.agent_tools import (
    AGENT_TOOLS,
    TOOL_SCHEMAS,
    AgentToolContext,
    execute_agent_tool,
)
from src.knowledge import DocChunk, NumpyVectorStore
from src.states import TutorState


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


class _Deps:
    def __init__(self, store=None, embedder=None, settings=None):
        self.store = store
        self.embedder = embedder or FakeEmbedder()
        self.settings = settings


def _ctx(state, deps=None, llm=None):
    return AgentToolContext(state=state, deps=deps or _Deps(), llm_call=llm)


def _store(embedder):
    store = NumpyVectorStore("t", embedder)
    store.add([
        DocChunk(
            id="c1",
            text="Параграф 12: Атмосфера. Атмосфера — воздушная оболочка Земли, азот 78% и кислород 21%.",
            section_number="12", section_title="Атмосфера", source="book", subject="география", grade="6",
        )
    ])
    return store


class TestCatalog:
    def test_all_tools_have_schema(self):
        names = set(AGENT_TOOLS)
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert names == schema_names
        assert len(TOOL_SCHEMAS) >= 10

    def test_schemas_openai_format(self):
        for s in TOOL_SCHEMAS:
            assert s["type"] == "function"
            fn = s["function"]
            assert fn["name"] and fn["description"]
            assert fn["parameters"]["type"] == "object"


class TestInterviewTools:
    def test_interview_progress(self):
        st = TutorState(learner_type="student")
        res, _ = execute_agent_tool("interview_progress", {}, _ctx(st))
        data = json.loads(res)
        assert data["ok"] is True
        assert "subject" in data["missing_fields"]
        assert "learner_type" in data["filled_fields"]
        assert data["next_question"]

    def test_set_intake_applies_field(self):
        st = TutorState()
        ctx = _ctx(st)
        res, new_state = execute_agent_tool("set_intake", {"field": "learner_type", "value": "студент"}, ctx)
        assert new_state.learner_type == "student"
        data = json.loads(res)
        assert data["accepted"] is True
        assert data["progress"] == 1

    def test_set_intake_unknown_field(self):
        res, _ = execute_agent_tool("set_intake", {"field": "nope", "value": "x"}, _ctx(TutorState()))
        assert json.loads(res)["ok"] is False

    def test_extract_intake_fields_tool(self):
        res, _ = execute_agent_tool(
            "extract_intake_fields", {"text": "я в 7 классе, география, учебника нет, хочу квиз"}, _ctx(TutorState())
        )
        fields = json.loads(res)["fields"]
        assert fields["grade"] == "7"
        assert fields["mode"] == "quiz"


class TestRetrievalTools:
    def test_rag_search_returns_chunks(self):
        emb = FakeEmbedder()
        store = _store(emb)
        ctx = _ctx(TutorState(subject="география"), deps=_Deps(store=store, embedder=emb))
        res, _ = execute_agent_tool("rag_search", {"query": "Атмосфера", "k": 2}, ctx)
        data = json.loads(res)
        assert data["ok"] is True
        assert data["count"] >= 1
        assert "Атмосфера" in data["results"][0]["text"]

    def test_rag_search_empty_store_message(self):
        emb = FakeEmbedder()
        store = NumpyVectorStore("empty", emb)
        ctx = _ctx(TutorState(subject="география"), deps=_Deps(store=store, embedder=emb))
        res, _ = execute_agent_tool("rag_search", {"query": "ничего"}, ctx)
        data = json.loads(res)
        assert data["ok"] is True
        assert data["results"] == []
        assert "route_to_source" in data["message"]

    def test_get_knowledge_graph(self):
        st = TutorState(knowledge_graph={"nodes": [{"id": "s1", "title": "Атмосфера", "type": "topic"}], "edges": []})
        res, _ = execute_agent_tool("get_knowledge_graph", {}, _ctx(st))
        data = json.loads(res)
        assert data["nodes"][0]["title"] == "Атмосфера"


class TestTutoringTools:
    _GEN = '{"question": "Что такое атмосфера?", "options": null, "answer_type": "open", "topic": "Атмосфера"}'

    def test_generate_quiz_sets_current_question(self):
        emb = FakeEmbedder()
        store = _store(emb)
        st = TutorState(subject="география", topic="Атмосфера", mode="quiz")
        ctx = _ctx(st, deps=_Deps(store=store, embedder=emb), llm=lambda m: self._GEN)
        res, new_state = execute_agent_tool("generate_quiz", {}, ctx)
        data = json.loads(res)
        assert data["ok"] is True
        assert data["question"] == "Что такое атмосфера?"
        assert new_state.current_question is not None
        assert new_state.asked_questions == ["Что такое атмосфера?"]

    def test_generate_lesson_sets_lesson_text(self):
        emb = FakeEmbedder()
        store = _store(emb)
        st = TutorState(subject="география", topic="Атмосфера", mode="lesson")
        ctx = _ctx(st, deps=_Deps(store=store, embedder=emb),
                   llm=lambda m: '{"text": "Атмосфера — газовая оболочка Земли."}')
        res, new_state = execute_agent_tool("generate_lesson", {}, ctx)
        data = json.loads(res)
        assert data["ok"] is True
        assert "оболочка" in data["text"]
        assert new_state.lesson_done is True

    def test_route_to_source_signal(self):
        res, _ = execute_agent_tool("route_to_source", {"reason": "нет контекста"}, _ctx(TutorState()))
        data = json.loads(res)
        assert data["ok"] is True
        assert data["action"] == "route_to_source"

    def test_finish_session(self):
        res, new_state = execute_agent_tool("finish_session", {}, _ctx(TutorState()))
        data = json.loads(res)
        assert data["action"] == "finish_session"
        assert new_state.session_status == "completed"
        assert new_state.quiz_complete is True

    def test_give_hint_tool(self):
        from api.schemas import QuizCard
        st = TutorState(subject="t", topic="Тема", mode="quiz",
                        current_question=QuizCard(question_id="q1", question="Вопрос?",
                                                  options=None, answer_type="open",
                                                  difficulty="medium", topic="Тема"),
                        current_answers=["ключевой правильный ответ"])
        res, out = execute_agent_tool("give_hint", {"level": 1}, _ctx(st))
        data = json.loads(res)
        assert data["ok"] is True
        assert "hint" in res
        assert out.hint_level == 1
        assert out.retry_question_id == "q1"

    def test_give_hint_no_active_question(self):
        res, _ = execute_agent_tool("give_hint", {}, _ctx(TutorState()))
        assert json.loads(res)["ok"] is False

    def test_unknown_tool_error(self):
        res, _ = execute_agent_tool("no_such_tool", {}, _ctx(TutorState()))
        assert json.loads(res)["ok"] is False
