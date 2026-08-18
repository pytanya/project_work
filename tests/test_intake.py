"""Тесты intake-фазы (Слайс 3): чек-лист, validate_intake, прогресс (В-3)."""

from __future__ import annotations

import pytest

from src.intake import (
    apply_answer,
    compute_missing,
    emergency_min_set_met,
    normalize_answer,
    validate_intake,
)
from src.states import IntakeState


class TestNormalizeAnswer:
    def test_learner_type(self):
        assert normalize_answer("learner_type", "Я ученик 6 класса") == "schoolchild"
        assert normalize_answer("learner_type", "студент") == "student"

    def test_grade(self):
        assert normalize_answer("grade", "6 класс") == "6"
        assert normalize_answer("grade", "11") == "11"

    def test_has_textbook(self):
        assert normalize_answer("has_textbook", "да") is True
        assert normalize_answer("has_textbook", "нет") is False
        assert normalize_answer("has_textbook", "не знаю") is None

    def test_mode(self):
        assert normalize_answer("mode", "квиз") == "quiz"
        assert normalize_answer("mode", "объяснение") == "explain"
        assert normalize_answer("mode", "глубокий разбор") == "deep_dive"

    def test_topic(self):
        assert normalize_answer("topic", "Атмосфера") == "Атмосфера"
        # ответ-режим не должен попасть в тему (поиск по теме, не по «квиз»)
        assert normalize_answer("topic", "квиз") is None
        assert normalize_answer("topic", "урок") is None
        assert normalize_answer("topic", "объяснение") is None
        # yes/no — не тема
        assert normalize_answer("topic", "нет") is None
        # «все» = весь учебник
        assert normalize_answer("topic", "все") == "all"
        assert normalize_answer("topic", "весь учебник") == "all"

    def test_unknown_is_none(self):
        assert normalize_answer("mode", "хз") is None
        assert normalize_answer("grade", "не знаю") is None


class TestComputeMissing:
    def test_empty_state(self):
        missing = compute_missing(IntakeState())
        assert "learner_type" in missing
        assert "subject" in missing
        assert "has_textbook" in missing
        assert "mode" in missing

    def test_schoolchild_requires_grade(self):
        s = IntakeState(learner_type="schoolchild", subject="география", has_textbook=False, mode="quiz")
        assert "grade" in compute_missing(s)

    def test_student_does_not_require_grade(self):
        s = IntakeState(learner_type="student", subject="физика", has_textbook=False, mode="quiz")
        assert "grade" not in compute_missing(s)

    def test_topic_counts_as_subject_alt(self):
        s = IntakeState(learner_type="student", topic="Атмосфера", has_textbook=False, mode="quiz")
        assert "subject" not in compute_missing(s)

    def test_topic_required_when_subject_set(self):
        """Тема обязательна, если задан предмет (поиск по теме, не по предмету)."""
        s = IntakeState(learner_type="student", subject="география", has_textbook=False, mode="quiz")
        assert "topic" in compute_missing(s)
        s.topic = "Атмосфера"
        assert "topic" not in compute_missing(s)


class TestApplyAnswer:
    def test_progress_tracking(self):
        s = IntakeState()
        s = apply_answer(s, "learner_type", "студент")
        assert s.learner_type == "student"
        assert s.intake_iterations == 1
        assert s.intake_progress == 1
        assert s.intake_no_progress_streak == 0

    def test_no_progress_on_ne_znayu(self):
        s = IntakeState(learner_type="student", subject="география", has_textbook=None, mode="quiz")
        s = apply_answer(s, "has_textbook", "не знаю")
        assert s.has_textbook is None
        assert s.intake_progress == 0
        assert s.intake_no_progress_streak == 1
        s = apply_answer(s, "has_textbook", "не знаю")
        assert s.intake_no_progress_streak == 2


class TestValidateIntake:
    def _full_state(self) -> IntakeState:
        s = IntakeState()
        for field, value in [
            ("learner_type", "ученик 6 класса"),
            ("grade", "6"),
            ("subject", "география"),
            ("topic", "Атмосфера"),
            ("has_textbook", "нет"),
            ("mode", "квиз"),
        ]:
            s = apply_answer(s, field, value)
        return s

    def test_start_when_complete(self):
        s = self._full_state()
        d = validate_intake(s, max_iterations=3)
        assert d.decision == "start"
        assert d.missing_fields == []

    def test_ask_when_missing(self):
        s = IntakeState(learner_type="student")
        d = validate_intake(s, max_iterations=3)
        assert d.decision == "ask"
        assert d.missing_fields
        assert d.next_question  # вопрос не пуст

    def test_emergency_via_iteration_limit(self):
        s = self._full_state()
        s.has_textbook = None  # убрали обязательное поле
        d = validate_intake(s, max_iterations=2)
        # итераций уже >= лимита (6 при лимите 2) → экстренный старт
        assert d.decision == "emergency_start"
        assert d.warning

    def test_emergency_via_no_progress_streak(self):
        s = IntakeState(learner_type="student", subject="география", mode="quiz")
        s = apply_answer(s, "has_textbook", "не знаю")
        s = apply_answer(s, "has_textbook", "не знаю")
        d = validate_intake(s, max_iterations=3)
        assert d.decision == "emergency_start"
        assert "прогресса" in d.warning or "без прогресса" in d.warning

    def test_emergency_min_set(self):
        s = self._full_state()
        assert emergency_min_set_met(s) is True
        empty = IntakeState()
        assert emergency_min_set_met(empty) is False

    def test_scenario_ne_znayu_na_vse(self):
        """Пользователь отвечает «не знаю» на всё → 2 итерации без прогресса."""
        s = IntakeState()
        s = apply_answer(s, "learner_type", "не знаю")
        s = apply_answer(s, "subject", "не знаю")
        d = validate_intake(s, max_iterations=3)
        assert d.decision == "emergency_start"
