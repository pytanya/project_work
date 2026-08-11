"""Тесты графа агента (Слайс 7): intake → источник → квиз → оценка → сводка."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from src.config import BASE_DIR, Settings
from src.graph import GraphDeps, build_graph
from src.knowledge import DocChunk, NumpyVectorStore
from src.states import TutorState

FGOS_DIR = BASE_DIR / "data" / "fgos_reference"

_GEN = '{"question": "Что такое атмосфера?", "options": null, "answer_type": "open", "topic": "Атмосфера"}'
_EVAL_OK = '{"score": 8, "correct": true, "feedback": "Верно!", "citation_ok": true}'
_EVAL_WRONG = '{"score": 2, "correct": false, "feedback": "Неверно.", "citation_ok": false}'
_EXPL = '{"text": "Атмосфера — газовая оболочка.", "citation": {"paragraph": "§12", "source": "учебник"}}'
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
def deps(make_settings, tmp_path):
    s = make_settings(
        FGOS_REFERENCE_DIR=str(FGOS_DIR),
        TEXTBOOKS_DOWNLOADS_DIR=str(tmp_path / "downloads"),
        MAX_INTAKE_ITERATIONS=8,
    )
    embedder = FakeEmbedder()
    store = NumpyVectorStore("t", embedder)
    store.add([
        DocChunk(
            id="c1",
            text="Параграф 12: Атмосфера. Атмосфера — воздушная оболочка Земли, состоит из азота (78%) и кислорода (21%).",
            section_number="12", section_title="Атмосфера", source="book", subject="география", grade="6",
        )
    ])
    return GraphDeps(
        embedder=embedder,
        store=store,
        settings=s,
        tutor_llm=lambda m: _GEN,
        eval_llm=lambda m: _EVAL_OK,
        expert_llm=lambda m: _EXPL,
        judge_llm=lambda m: _JUDGE,
    )


def _invoke(graph, state_dict, config=None):
    """invoke + валидация результата в TutorState."""
    return TutorState.model_validate(graph.invoke(state_dict, config=config))


def _feed(graph, state, answers):
    """Последовательно подаёт ответы пользователя, возвращает финальное состояние."""
    res = _invoke(graph, state.model_dump())
    for ans in answers:
        res = _invoke(graph, {**res.model_dump(), "pending_answer": ans})
    return res


class TestIntakeFlow:
    def test_asks_learner_type_first(self, deps):
        graph = build_graph(deps)
        res = _invoke(graph, TutorState().model_dump())
        assert res.intake_field == "learner_type"
        assert res.agent_question

    def test_full_intake_to_quiz(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=3, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["ученик 6 класса", "6", "география", "нет", "квиз"])
        # intake завершён, источник готов (pre-seeded), задан вопрос квиза
        assert res.agent_question
        assert res.current_question is not None
        assert res.source_status == "ready"
        # curriculum вычисляется только при наличии темы; иначе None
        assert res.curriculum in (None, "ГЕОГ.5-6.3.1", "unverified")

    def test_emergency_start_on_ne_znayu(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["не знаю", "не знаю"])
        assert res.agent_question or res.current_question
        assert res.intake_field is None


class TestQuizFlow:
    def test_full_quiz_with_knowledge_map_and_summary(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=3, sources=[{"type": "web", "url": "x"}], collection_id="web")
        # intake
        res = _feed(graph, state, ["студент", "география", "нет", "квиз"])
        # q1 (оценка ОК)
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Атмосфера — это воздушная оболочка."})
        assert res.knowledge_map.get("Атмосфера") is not None
        assert res.agent_message
        # q2
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Воздушная оболочка Земли."})
        assert res.correct_count >= 1
        # q3
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Океан."})
        assert res.quiz_complete is True
        assert res.session_status == "completed"
        assert res.summary_text and "Квиз завершён" in res.summary_text

    def test_wrong_answer_triggers_explanation(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "нет", "квиз"])
        deps.eval_llm = lambda m: _EVAL_WRONG
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Неправильный ответ тут"})
        assert "Объяснение:" in res.agent_message
        assert "§12" in res.agent_message


class TestSourceFlow:
    def test_source_failed_path(self, deps, monkeypatch):
        class Failed:
            status = "failed"
            failed_reason = "empty_result"
            message = "Материалы по теме не найдены"
            sources = []
            texts = []

        monkeypatch.setattr(
            "src.graph.source_finder.collect_source_materials", lambda **kw: Failed()
        )
        graph = build_graph(deps)
        state = TutorState(num_questions=1)
        res = _feed(graph, state, ["студент", "физика", "нет", "квиз"])
        assert res.session_status == "failed"
        assert res.source_status == "failed"
        assert res.agent_message

    def test_textbook_file_path(self, deps, tmp_path):
        doc = tmp_path / "doc.txt"
        doc.write_text(
            "Параграф 12: Атмосфера\nСтроение атмосферы.\n\nПараграф 13: Погода\nПогода меняется.",
            encoding="utf-8",
        )
        graph = build_graph(deps)
        state = TutorState(num_questions=1, textbook_file=str(doc))
        res = _feed(graph, state, ["студент", "география", "да", "квиз"])
        # после upload файл индексируется, задаётся вопрос квиза
        assert res.source_status == "ready"
        assert res.agent_question
        assert res.current_question is not None


class TestBuild:
    def test_compiles_with_memory_checkpointer(self, deps):
        from langgraph.checkpoint.memory import MemorySaver

        graph = build_graph(deps, checkpointer=MemorySaver())
        res = _invoke(
            graph,
            TutorState().model_dump(),
            config={"configurable": {"thread_id": "t1"}},
        )
        assert res.intake_field == "learner_type"
