"""Тесты intake-фазы (Слайс 3): чек-лист, validate_intake, прогресс (В-3)."""

from __future__ import annotations

import pytest

from src.intake import (
    apply_answer,
    apply_intake_card,
    build_intake_card,
    compute_missing,
    emergency_min_set_met,
    extract_intake_fields,
    maybe_start_card,
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


class TestExtractIntakeFields:
    """Много-полевое извлечение из свободного ответа (5.4 / инструмент set_intake)."""

    def test_full_compound_answer(self):
        fields = extract_intake_fields("я в 7 классе, география, атмосфера, учебника нет, хочу квиз")
        assert fields["learner_type"] == "schoolchild"
        assert fields["grade"] == "7"
        assert fields["subject"] == "география"
        assert fields["has_textbook"] is False
        assert fields["mode"] == "quiz"

    def test_student_with_marker_topic(self):
        fields = extract_intake_fields("студент, тема Атмосфера, квиз")
        assert fields["learner_type"] == "student"
        assert fields["topic"] == "Атмосфера"
        assert fields["mode"] == "quiz"

    def test_has_textbook_yes(self):
        assert extract_intake_fields("да")["has_textbook"] is True
        assert extract_intake_fields("есть учебник")["has_textbook"] is True

    def test_unknown_returns_empty(self):
        assert extract_intake_fields("не знаю") == {}
        assert extract_intake_fields("") == {}

    def test_topic_not_extracted_without_marker(self):
        # «Атмосфера» без маркера — тема не распознаётся (остаётся на уточняющий вопрос)
        assert "topic" not in extract_intake_fields("Атмосфера")

    def test_mode_not_extracted_from_topic(self):
        # ответ «квиз» на вопрос о теме не должен стать и темой и режимом в одном поле
        fields = extract_intake_fields("хочу квиз")
        assert fields.get("mode") == "quiz"
        assert "topic" not in fields

    def test_all_topic_segment(self):
        # «все» сегментом в составном ответе → topic="all"
        fields = extract_intake_fields("студент, философия, все, нет, квиз")
        assert fields.get("topic") == "all"
        assert extract_intake_fields("все")["topic"] == "all"
        assert extract_intake_fields("весь учебник")["topic"] == "all"

    def test_no_segment_has_textbook(self):
        # «нет» сегментом в составном ответе → has_textbook=False
        fields = extract_intake_fields("студент, философия, все, нет, квиз")
        assert fields.get("has_textbook") is False


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


class TestIntakeCard:
    def test_build_card_has_all_fields(self):
        s = IntakeState()
        card = build_intake_card(s)
        keys = [f["key"] for f in card["fields"]]
        assert keys == ["name", "learner_type", "grade", "subject", "topic", "has_textbook", "mode"]
        assert card["title"]
        assert card["question"]

    def test_build_card_prefills_profile(self):
        s = IntakeState(student_name="Маша", learner_type="schoolchild", grade="6",
                        subject="география", topic="Атмосфера", mode="quiz")
        card = build_intake_card(s)
        by_key = {f["key"]: f["value"] for f in card["fields"]}
        assert by_key["name"] == "Маша"
        assert by_key["learner_type"] == "schoolchild"
        assert by_key["grade"] == "6"
        assert by_key["mode"] == "quiz"

    def test_apply_card_fills_all(self):
        s = IntakeState(student_id="stu_1")
        st = apply_intake_card(s, {
            "name": "Маша", "learner_type": "schoolchild", "grade": "6",
            "subject": "география", "topic": "Атмосфера",
            "has_textbook": "false", "mode": "quiz",
        })
        assert st.student_name == "Маша"
        assert st.learner_type == "schoolchild"
        assert st.grade == "6"
        assert st.subject == "география"
        assert st.topic == "Атмосфера"
        assert st.has_textbook is False
        assert st.mode == "quiz"
        assert st.agent_card is None  # карточка сброшена
        assert compute_missing(st) == []  # чек-лист заполнен

    def test_apply_card_partial_keeps_missing(self):
        s = IntakeState(student_name="Петя", learner_type="student")
        st = apply_intake_card(s, {"subject": "физика"})
        assert st.subject == "физика"
        assert "topic" in compute_missing(st)

    def test_apply_card_rebind_student_id(self):
        s = IntakeState(student_id="stu_old")
        st = apply_intake_card(
            s,
            {"name": "Татьяна Петрова", "learner_type": "student",
             "subject": "физика", "topic": "Оптика", "has_textbook": "true", "mode": "lesson"},
            student_id="stu_new",
        )
        assert st.student_id == "stu_new"
        # без изменения передаваемого идентичности — старый id сохраняется
        st2 = apply_intake_card(s, {"subject": "физика"}, student_id="stu_old")
        assert st2.student_id == "stu_old"

    def test_maybe_start_card_only_fresh(self):
        s = IntakeState()
        st, started = maybe_start_card(s)
        assert started is True
        assert st.agent_card is not None
        # повторный вызов — карточка уже есть
        st2, started2 = maybe_start_card(st)
        assert started2 is False
        # после ответа (итераций > 0) — карточку не создаём
        s3 = IntakeState()
        s3.intake_iterations = 1
        st3, started3 = maybe_start_card(s3)
        assert started3 is False
        assert st3.agent_card is None
