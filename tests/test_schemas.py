"""Тесты Pydantic-схем API EduTutor (Слайс 0, В-4).

Схемы — часть MVP: IntakeStatusResponse, QuizCard, MessageResponse, WsEvent.
Проверяется структура, литеральные типы и совместимость с разделом 8.2–8.3.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import IntakeStatusResponse, MessageResponse, QuizCard, WsEvent


class TestIntakeStatusResponse:
    def test_defaults(self):
        r = IntakeStatusResponse()
        assert r.missing_fields == []
        assert r.next_question == ""
        assert r.complete is False

    def test_full(self):
        r = IntakeStatusResponse(
            missing_fields=["grade", "subject"],
            next_question="Укажите класс?",
            complete=False,
        )
        assert r.missing_fields == ["grade", "subject"]
        assert r.next_question == "Укажите класс?"


class TestQuizCard:
    def test_open_question_without_options(self):
        c = QuizCard(
            question_id="q1",
            question="Что такое атмосфера?",
            options=None,
            answer_type="open",
            difficulty="easy",
            topic="Атмосфера",
        )
        assert c.options is None
        assert c.answer_type == "open"

    def test_single_with_options(self):
        c = QuizCard(
            question_id="q2",
            question="Выберите верный ответ",
            options=["А", "Б", "В"],
            answer_type="single",
            difficulty="medium",
            topic="Литосфера",
        )
        assert len(c.options) == 3

    def test_invalid_answer_type_rejected(self):
        with pytest.raises(ValidationError):
            QuizCard(
                question_id="q",
                question="?",
                answer_type="essay",
                difficulty="easy",
                topic="t",
            )

    def test_invalid_difficulty_rejected(self):
        with pytest.raises(ValidationError):
            QuizCard(
                question_id="q",
                question="?",
                answer_type="open",
                difficulty="expert",
                topic="t",
            )


class TestMessageResponse:
    def test_types(self):
        for t in [
            "intake_question", "source_progress", "quiz_card",
            "explanation", "summary", "system", "error",
        ]:
            m = MessageResponse(type=t, payload={})
            assert m.type == t

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            MessageResponse(type="banana", payload={})


class TestWsEvent:
    def test_events(self):
        for e in [
            "intake.question", "source.progress", "source.failed",
            "quiz.card", "tutor.explanation", "tutor.summary", "session.error",
        ]:
            ev = WsEvent(event=e, data={})
            assert ev.event == e

    def test_quiz_card_event_payload(self):
        card = QuizCard(
            question_id="q1", question="?", answer_type="single",
            difficulty="medium", topic="Тема",
        )
        ev = WsEvent(event="quiz.card", data=card.model_dump())
        assert ev.data["question_id"] == "q1"

    def test_invalid_event_rejected(self):
        with pytest.raises(ValidationError):
            WsEvent(event="nope.event", data={})
