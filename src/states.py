"""
EduTutor — Pydantic-модели состояний графа (раздел 5.1 SPECIFICATION.md).

IntakeState — состояние intake-фазы (чек-лист).
TutorState — полное состояние графа: intake + поиск источника + тьюторинг.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from api.schemas import QuizCard

MODE_LITERAL = Literal["quiz", "explain", "deep_dive"]
DIFFICULTY_LITERAL = Literal["easy", "medium", "hard"]


class IntakeState(BaseModel):
    """Состояние intake-фазы — собранная информация об обучаемом (раздел 5.1)."""

    learner_type: Optional[Literal["student", "schoolchild"]] = None
    grade: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    topic: Optional[str] = None
    has_textbook: Optional[bool] = None
    textbook_file: Optional[str] = None
    textbook_author: Optional[str] = None
    textbook_url: Optional[str] = None
    chapter: Optional[str] = None
    mode: Optional[MODE_LITERAL] = None
    difficulty: DIFFICULTY_LITERAL = "medium"
    num_questions: int = 10
    intake_iterations: int = 0
    intake_progress: int = 0
    intake_no_progress_streak: int = 0
    missing_fields: List[str] = Field(default_factory=list)


class TutorState(IntakeState):
    """Полное состояние графа агента (intake → источник → тьюторинг)."""

    # --- Поиск источника (Ж-5) ---
    textbook_status: Optional[str] = None      # "confirmed" | "unconfirmed" | None
    source_status: Optional[str] = None        # pending | searching | ready | failed
    source_note: Optional[str] = None          # причина/источник
    sources: List[Dict[str, Any]] = Field(default_factory=list)

    # --- RAG ---
    collection_id: Optional[str] = None

    # --- Тьюторинг (Ж-6) ---
    knowledge_map: Dict[str, float] = Field(default_factory=dict)
    asked_questions: List[str] = Field(default_factory=list)
    current_question: Optional[QuizCard] = None
    correct_count: int = 0
    answered_count: int = 0
    correct_streak: int = 0
    wrong_streak: int = 0
    total_llm_calls: int = 0

    # --- Сессия ---
    session_status: Optional[Literal["active", "completed", "failed"]] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)

    # --- Исполнение графа (ввод/вывод между итерациями) ---
    pending_answer: Optional[str] = None       # ответ пользователя на текущий вопрос
    intake_field: Optional[str] = None         # поле чек-листа, которое сейчас уточняем
    agent_question: Optional[str] = None       # текст вопроса агенту-пользователю
    agent_options: Optional[List[str]] = None  # варианты ответа (для квиза)
    agent_message: Optional[str] = None        # сообщение пользователю (фидбек/суммари)
    quiz_complete: bool = False
    summary_text: Optional[str] = None
    last_judge_score: Optional[float] = None   # avg балл судьи по контракту «оценка» (для eval)

    def update_knowledge(self, topic: str, score01: float) -> None:
        """Экспоненциальное сглаживание мастерства (Ж-6): 0.7*текущее + 0.3*результат."""
        current = self.knowledge_map.get(topic, 0.5)
        self.knowledge_map[topic] = round(0.7 * current + 0.3 * score01, 4)
