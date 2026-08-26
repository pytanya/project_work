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


class LessonSection(BaseModel):
    """Секция урока: заголовок + тело + цитата-источник + микро-проверка «Проверь себя»."""

    heading: str = ""
    body: str = ""
    citation: str = ""          # «§N» / страница / название источника
    source: str = ""            # URL/имя источника, из которого взят материал
    check_question: str = ""    # короткий вопрос на понимание после секции


class DiagramNode(BaseModel):
    """Узел схемы: короткая подпись + (для kind='map') позиция x,y в нормализованных координатах 0..1."""

    id: str = ""
    label: str = ""
    x: Optional[float] = None
    y: Optional[float] = None


class DiagramEdge(BaseModel):
    """Связь схемы: от → к. color — семантическое противопоставление
    (warm/cold — контрастные роли рёбер: тёплое/холодное, причина/следствие, сильное/слабое)."""

    source: str = ""
    target: str = ""
    label: str = ""
    color: Literal["warm", "cold", "neutral"] = "neutral"


class LessonDiagram(BaseModel):
    """Схема-иллюстрация к уроку (dual-coding).

    kind:
      - flow  — этапы / причина→следствие (боксы и стрелки);
      - cycle — круговорот (расположение по кругу);
      - map   — пространственная схема (узлы с координатами 0..1, стрелки по направлениям).

    Инвариант: узлы и связи отражают ТОЛЬКО те же факты и термины, что и секции урока —
    диаграмма не вводит новых понятий (нет противоречий с текстом).
    """

    kind: Literal["flow", "cycle", "map"] = "flow"
    title: str = ""
    nodes: List[DiagramNode] = Field(default_factory=list)
    edges: List[DiagramEdge] = Field(default_factory=list)


class Lesson(BaseModel):
    """Структурированный урок (вместо стены текста).

    Для режимов прямого стриминга (markdown) используется поле raw_text,
    которое рендерится на фронтенде как plain lesson с абзацами.
    
    Поля ниже заполняются автоматически из markdown при парсинге:
    - hook — зацепка-вопрос в начале (активация внимания);
    - definition — короткое определение темы;
    - key_terms — ключевые термины с краткими определениями (глоссарий);
    - diagram — схема-иллюстрация (dual-coding, не противоречит секциям);
    - sections — 2-3 секции по одному под-вопросу, каждая с цитатой и «Проверь себя»;
    - summary — итог в 1-2 предложения.
    """

    title: str = ""
    hook: str = ""
    definition: str = ""
    key_terms: List[Dict[str, str]] = Field(default_factory=list)
    diagram: Optional[LessonDiagram] = None
    sections: List[LessonSection] = Field(default_factory=list)
    summary: str = ""
    raw_text: str = ""  # Сырой markdown-текст урока (для стриминга/резервного рендеринга)

    def render_text(self) -> str:
        """Полный текст урока (для lesson_text / стриминга / dedupe / resync)."""
        parts = []
        if self.hook:
            parts.append(self.hook)
        if self.definition:
            parts.append(self.definition)
        for s in self.sections:
            if s.heading:
                parts.append(s.heading)
            if s.body:
                parts.append(s.body)
        if self.summary:
            parts.append(self.summary)
        return "\n\n".join(p for p in parts if p)


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
        "intake.card",
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
