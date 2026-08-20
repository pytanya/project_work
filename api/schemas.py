"""EduTutor — Pydantic-схемы API (раздел 8.2 SPECIFICATION.md).

Схемы — часть MVP (решено заказчиком): определяются и покрываются тестами,
сервер FastAPI/WebSocket — расширение (раздел 8, Этап 5).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class IntakeStatusResponse(BaseModel):
    """Ответ intake-эндпоинтов — закрывает разрыв API ↔ IntakeWizard (missing_fields)."""

    missing_fields: List[str] = Field(default_factory=list)
    next_question: str = Field(default="")
    complete: bool = Field(default=False)


class QuizCard(BaseModel):
    """Карточка вопроса квиза (WS-событие quiz.card / POST /message payload)."""

    question_id: str
    question: str
    options: Optional[List[str]] = None
    answer_type: Literal["single", "multiple", "open"]
    difficulty: Literal["easy", "medium", "hard"]
    topic: str


class MessageResponse(BaseModel):
    """Единая схема ответа POST /message — тип + полезная нагрузка."""

    type: Literal[
        "intake_question",
        "source_progress",
        "quiz_card",
        "lesson",
        "explanation",
        "summary",
        "system",
        "error",
    ]
    payload: Dict[str, Any] = Field(default_factory=dict)


class WsEvent(BaseModel):
    """Событие WebSocket — primary-канал стриминга событий агента (раздел 8.3)."""

    event: Literal[
        "intake.question",
        "source.progress",
        "source.failed",
        "quiz.card",
        "tutor.lesson",
        "tutor.explanation",
        "tutor.summary",
        "token",
        "graph.ready",
        "wiki.updated",
        "system",
        "system.heartbeat",
        "session.error",
    ]
    data: Dict[str, Any] = Field(default_factory=dict)
