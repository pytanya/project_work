"""Тесты тьюторинг-цикла (Слайс 6): генерация, оценка, адаптация, knowledge_map."""

from __future__ import annotations

import pytest

from src.config import BASE_DIR, Settings
from src.states import TutorState
from src.tutor import (
    adjust_difficulty,
    difficulty_for_grade,
    evaluate_answer,
    explain_error,
    generate_question,
    grade_prompt,
    parse_llm_json,
    simplicity_precheck,
    update_knowledge_map,
)


class TestGradePrompt:
    def test_grade6_easy(self):
        assert "5-6" in grade_prompt("6")
        assert "простые" in grade_prompt("6")

    def test_grade8_medium(self):
        assert "7-9" in grade_prompt("8")

    def test_grade10_hard(self):
        assert "10-11" in grade_prompt("11")

    def test_student(self):
        assert "студент" in grade_prompt(None)

    def test_difficulty_for_grade(self):
        assert difficulty_for_grade("6") == "easy"
        assert difficulty_for_grade("8") == "medium"
        assert difficulty_for_grade("11") == "hard"
        assert difficulty_for_grade(None) == "medium"


class TestParseLlmJson:
    def test_plain(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert parse_llm_json('```json\n{"a": 2}\n```') == {"a": 2}

    def test_with_surrounding_text(self):
        assert parse_llm_json('Вот ответ: {"a": 3} готово') == {"a": 3}

    def test_invalid_returns_empty(self):
        assert parse_llm_json("не json совсем") == {}
        assert parse_llm_json("") == {}


def _state(**kw) -> TutorState:
    return TutorState(learner_type="student", subject="география", has_textbook=False, mode="quiz", **kw)


class TestGenerateQuestion:
    def test_valid_card_from_llm(self):
        state = _state(grade="6", curriculum="ФГОС")
        fake = lambda msgs: '{"question": "Что такое атмосфера?", "options": ["газ", "жидкость"], "answer_type": "single", "topic": "Атмосфера"}'
        card = generate_question("Атмосфера", ["Атмосфера — оболочка Земли."], "easy", state, llm_call=fake)
        assert card.question_id == "q1"
        assert card.answer_type == "single"
        assert card.difficulty == "easy"
        assert card.topic == "Атмосфера"
        assert state.asked_questions == ["q1"]
        assert state.current_question is card

    def test_fallback_when_llm_garbage(self):
        state = _state()
        card = generate_question("Атмосфера", ["текст"], "medium", state, llm_call=lambda m: "не json")
        assert card.answer_type == "open"
        assert "Атмосфера" in card.question

    def test_open_question_options_none(self):
        state = _state()
        fake = lambda m: '{"question": "Опишите", "options": null, "answer_type": "open", "topic": "Тема"}'
        card = generate_question("Тема", ["контекст"], "hard", state, llm_call=fake)
        assert card.options is None


class TestSimplicityPrecheck:
    def test_short_answer_rejected(self):
        assert simplicity_precheck("да", ["Атмосфера — воздушная оболочка Земли."]) is False

    def test_long_without_terms_rejected(self):
        answer = "x" * 100
        assert simplicity_precheck(answer, ["Атмосфера — воздушная оболочка Земли."]) is False

    def test_long_with_term_accepted(self):
        answer = "Атмосфера — это воздушная оболочка Земли, и она важна."
        assert simplicity_precheck(answer, ["Атмосфера — воздушная оболочка Земли."]) is True


class TestEvaluateAnswer:
    def test_precheck_fail_short(self):
        state = _state()
        graded = evaluate_answer("Вопрос", "да", ["Атмосфера — воздушная оболочка."], state)
        assert graded.precheck_passed is False
        assert graded.model_used == "rule-based"
        assert graded.correct is False

    def test_llm_evaluation(self):
        state = _state()
        fake = lambda m: '{"score": 8, "correct": true, "feedback": "Верно!", "citation_ok": true}'
        answer = "Атмосфера — это воздушная оболочка Земли."
        graded = evaluate_answer("Вопрос", answer, ["Атмосфера — воздушная оболочка."], state, llm_call=fake)
        assert graded.precheck_passed is True
        assert graded.score == pytest.approx(0.8)
        assert graded.correct is True
        assert graded.citation_ok is True

    def test_expert_for_long_answer(self):
        state = _state()
        fake = lambda m: '{"score": 9, "correct": true, "feedback": "ok", "citation_ok": false}'
        long_answer = "Атмосфера — это воздушная оболочка, " + "и рассуждение " * 200
        graded = evaluate_answer("Вопрос", long_answer, ["Атмосфера — воздушная оболочка."], state, llm_call=fake)
        assert graded.model_used == "expert"  # Ж-8: развёрнутый ответ → эксперт


class TestAdjustDifficulty:
    def test_ups_after_3_correct(self):
        state = _state(difficulty="easy")
        for _ in range(3):
            adjust_difficulty(state, True)
        assert state.difficulty == "medium"

    def test_down_after_2_wrong(self):
        state = _state(difficulty="medium")
        adjust_difficulty(state, False)
        assert state.difficulty == "medium"
        adjust_difficulty(state, False)
        assert state.difficulty == "easy"

    def test_no_change_at_boundaries(self):
        state = _state(difficulty="hard")
        for _ in range(5):
            adjust_difficulty(state, True)
        assert state.difficulty == "hard"
        state = _state(difficulty="easy")
        for _ in range(5):
            adjust_difficulty(state, False)
        assert state.difficulty == "easy"


class TestKnowledgeMap:
    def test_exponential_smoothing(self):
        state = _state()
        update_knowledge_map(state, "Атмосфера", 1.0)
        assert state.knowledge_map["Атмосфера"] == pytest.approx(0.7 * 0.5 + 0.3 * 1.0)
        update_knowledge_map(state, "Атмосфера", 0.0)
        expected = 0.7 * (0.7 * 0.5 + 0.3 * 1.0)
        assert state.knowledge_map["Атмосфера"] == pytest.approx(expected)


class TestExplainError:
    def test_returns_text_and_citation(self):
        state = _state()
        fake = lambda m: '{"text": "Ошибка в том, что атмосфера — газ.", "citation": {"paragraph": "§12", "source": "Алексеев"}}'
        result = explain_error("Вопрос", "плохой ответ", ["Атмосфера — оболочка."], state, llm_call=fake)
        assert "газ" in result["text"]
        assert result["citation"]["paragraph"] == "§12"

    def test_fallback_empty(self):
        state = _state()
        result = explain_error("Вопрос", "ответ", ["контекст"], state, llm_call=lambda m: "garbage")
        assert result["text"]
        assert isinstance(result["citation"], dict)


class TestRealTutorIntegration:
    """Интеграционный тест полного цикла (генерация → оценка → объяснение) на RouterAI."""

    @pytest.mark.skipif(
        not (BASE_DIR / ".env").exists() or not Settings().ROUTERAI_API_KEY,
        reason="Нет ROUTERAI_API_KEY",
    )
    def test_full_loop(self):
        state = TutorState(
            learner_type="schoolchild", grade="6", subject="география",
            topic="Атмосфера", has_textbook=False, mode="quiz",
        )
        context = ["Параграф 12: Атмосфера. Атмосфера — воздушная оболочка Земли, состоящая из азота (78%) и кислорода (21%)."]
        card = generate_question("Атмосфера", context, "easy", state)
        assert card.question
        assert card.difficulty == "easy"

        answer = "Атмосфера — воздушная оболочка Земли, состоящая из азота (78%) и кислорода (21%)."
        graded = evaluate_answer(card.question, answer, context, state)
        assert graded.precheck_passed is True
        assert graded.feedback
        assert graded.model_used in ("tutor", "expert")

        update_knowledge_map(state, card.topic, graded.score)
        assert state.knowledge_map.get(card.topic) is not None

        explanation = explain_error(card.question, "не знаю", context, state)
        assert explanation["text"]
