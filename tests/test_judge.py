"""Тесты судьи (Слайс 6, К-4): три контракта."""

from __future__ import annotations

import pytest

from src.judge import (
    EVALUATION_CRITERIA,
    EXPLANATION_CRITERIA,
    LESSON_CRITERIA,
    QUESTION_CRITERIA,
    judge_evaluation,
    judge_explanation,
    judge_lesson,
    judge_question,
)

_GOOD = '{"criteria": {"relevance": 9, "grade_fit": 8, "clarity": 7, "factual_ok": 10}, "comment": "ok"}'
_BAD = '{"criteria": {"relevance": 3, "grade_fit": 2, "clarity": 1, "factual_ok": 4}}'
_GOOD_EVAL = '{"criteria": {"grade_correct": 9, "feedback_ok": 8, "difficulty_fit": 7}, "comment": "ok"}'
_GOOD_LESSON = '{"criteria": {"groundedness": 9, "coherence": 8, "grade_fit": 8, "no_contradiction": 10}, "comment": "ok"}'


class TestJudgeQuestion:
    def test_pass(self):
        result = judge_question("Что?", "Тема", "6", judge_call=lambda m: _GOOD)
        assert result.contract == "question"
        assert set(result.criteria) == set(QUESTION_CRITERIA)
        assert result.avg_score >= 7.0
        assert result.verdict == "pass"

    def test_fail(self):
        result = judge_question("Что?", "Тема", "6", judge_call=lambda m: _BAD)
        assert result.avg_score < 7.0
        assert result.verdict == "fail"

    def test_garbage_returns_zero_fail(self):
        result = judge_question("Что?", "Тема", "6", judge_call=lambda m: "не json")
        assert result.avg_score == 0.0
        assert result.verdict == "fail"


class TestJudgeExplanation:
    def test_criteria(self):
        result = judge_explanation("объяснение", {"paragraph": "§12"}, "6", judge_call=lambda m: _GOOD)
        assert result.contract == "explanation"
        assert set(result.criteria) == set(EXPLANATION_CRITERIA)


class TestJudgeEvaluation:
    def test_criteria_and_pass(self):
        graded = {"score": 0.8, "correct": True, "feedback": "верно"}
        result = judge_evaluation("Вопрос", "ответ", graded, judge_call=lambda m: _GOOD_EVAL)
        assert result.contract == "evaluation"
        assert set(result.criteria) == set(EVALUATION_CRITERIA)
        assert result.verdict == "pass"

    def test_feedback_included_in_subject(self):
        captured = {}

        def fake(messages):
            captured["user"] = messages[-1]["content"]
            return _GOOD

        judge_evaluation("Вопрос", "ответ", {"score": 0.8, "correct": True, "feedback": "пояснение"}, judge_call=fake)
        assert "пояснение" in captured["user"]
        assert "Вопрос" in captured["user"]


class TestJudgeLesson:
    def test_criteria_and_pass(self):
        result = judge_lesson("урок про атмосферу", ["контекст"], "6", judge_call=lambda m: _GOOD_LESSON)
        assert result.contract == "lesson"
        assert set(result.criteria) == set(LESSON_CRITERIA)
        assert result.verdict == "pass"

    def test_groundedness_low_fails(self):
        bad = '{"criteria": {"groundedness": 3, "coherence": 6, "grade_fit": 6, "no_contradiction": 8}}'
        result = judge_lesson("выдуманные факты", ["контекст"], "6", judge_call=lambda m: bad)
        assert result.avg_score < 7.0
        assert result.verdict == "fail"

    def test_student_grade_guidance_in_subject(self):
        captured = {}

        def fake(messages):
            captured["user"] = messages[-1]["content"]
            return _GOOD_LESSON

        judge_lesson("урок", ["контекст"], None, judge_call=fake)
        assert "студент" in captured["user"]  # grade_prompt(None) — студенческий уровень
        assert "groundedness" in captured["user"]

    def test_zero_citations_cap_groundedness(self):
        """Судья не может «простить» урок без цитат: groundedness жёстко капается
        детерминированной оценкой eval_lesson (цитаты: 0/10) → вердикт fail,
        даже если LLM поставил все критерии высоко."""
        result = judge_lesson(
            "урок", ["контекст"], "6",
            judge_call=lambda m: _GOOD_LESSON,  # LLM даёт groundedness 9
            eval_criteria={"citations": 0.0},
        )
        assert result.criteria["groundedness"] <= 1.0
        assert result.avg_score < 7.0
        assert result.verdict == "fail"

    def test_partial_citations_partial_cap(self):
        """Половина секций с цитатой → groundedness капается до ~5, но не до нуля."""
        result = judge_lesson(
            "урок", ["контекст"], "6",
            judge_call=lambda m: '{"criteria": {"groundedness": 9, "coherence": 8, "grade_fit": 8, "no_contradiction": 10}}',
            eval_criteria={"citations": 0.5},
        )
        assert result.criteria["groundedness"] == 5.0
        assert result.criteria["coherence"] == 8.0

    def test_all_citations_no_cap(self):
        """Все секции процитированы → кап не применяется, судья вольный."""
        result = judge_lesson(
            "урок", ["контекст"], "6",
            judge_call=lambda m: _GOOD_LESSON,
            eval_criteria={"citations": 1.0},
        )
        assert result.criteria["groundedness"] == 9.0
        assert result.verdict == "pass"

    def test_eval_criteria_in_prompt(self):
        captured = {}

        def fake(messages):
            captured["user"] = messages[-1]["content"]
            return _GOOD_LESSON

        judge_lesson("урок", ["контекст"], "6", judge_call=fake,
                     eval_criteria={"citations": 0.0})
        assert "auto_eval" in captured["user"]
        assert "citations" in captured["user"]

    def test_judge_llm_offline_neutral(self, monkeypatch):
        """Офлайн: судья возвращает нейтральный вердикт, а не роняет поток."""
        from src.llm_client import LLMClient

        def boom(self, *a, **k):
            raise RuntimeError("Все провайдеры и модели недоступны")

        monkeypatch.setattr(LLMClient, "chat", boom)
        result = judge_question("q", "t", "6")  # judge_call=None → реальный клиент
        assert result.avg_score == 5.0
        assert result.verdict == "fail"


class TestContractsPrompts:
    def test_all_contracts_include_criteria_in_system(self):
        captured = {}

        def fake(messages):
            captured["system"] = messages[0]["content"]
            return _GOOD

        judge_question("q", "t", "6", judge_call=fake)
        assert "relevance" in captured["system"]
        assert "grade_fit" in captured["system"]

    def test_question_grade_guidance_in_user(self):
        captured = {}

        def fake(messages):
            captured["user"] = messages[-1]["content"]
            return _GOOD

        judge_question("q", "t", "6", judge_call=fake)
        assert "5-6" in captured["user"]  # grade_prompt(6)
