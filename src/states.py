"""
EduTutor — Pydantic-модели состояний графа (раздел 5.1 SPECIFICATION.md).

IntakeState — состояние intake-фазы (чек-лист).
TutorState — полное состояние графа: intake + поиск источника + тьюторинг.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from api.schemas import Lesson, QuizCard

MODE_LITERAL = Literal["quiz", "explain", "deep_dive", "lesson"]
DIFFICULTY_LITERAL = Literal["easy", "medium", "hard"]


class IntakeState(BaseModel):
    """Состояние intake-фазы — собранная информация об обучаемом (раздел 5.1)."""

    # Профиль ученика (персистентный, см. src/student.py): стабильный ID из
    # localStorage/CLI; name — имя, введённое в карточке знакомства.
    student_id: Optional[str] = None
    student_name: Optional[str] = None
    learner_type: Optional[Literal["student", "schoolchild"]] = None
    grade: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    topic: Optional[str] = None
    has_textbook: Optional[bool] = None
    textbook_file: Optional[str] = None
    textbook_name: Optional[str] = None       # оригинальное имя файла (стабильный ключ графа)
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
    intake_field: Optional[str] = None         # поле чек-листа, которое сейчас уточняем
    # Карточка intake (быстрое заполнение): структурированная форма вместо
    # пошагового Q&A. Поля: {title, question, fields: [{key, label, type, options, value}]}.
    agent_card: Optional[Dict[str, Any]] = None
    agent_question: Optional[str] = None   # текст вопроса агенту-пользователю

    # --- Сканированный учебник (3.2) ---
    textbook_scanned: bool = False
    textbook_pages: Optional[str] = None
    textbook_topic: Optional[str] = None
    doc_pages_attempts: int = 0
    page_offset: Optional[int] = None


class TutorState(IntakeState):
    """Полное состояние графа агента (intake → источник → тьюторинг)."""

    # --- Поиск источника (Ж-5) ---
    textbook_status: Optional[str] = None      # "confirmed" | "unconfirmed" | None
    source_status: Optional[str] = None        # pending | searching | ready | failed
    source_note: Optional[str] = None          # причина/источник
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    # Переиспользование материалов: по теме уже есть разобранные чанки → ждём решения
    # ученика (да — используем существующие, нет — ищем другие).
    reuse_pending: bool = False
    # Явная команда «Найти учебник»: обходим кэш материалов и ищем заново
    # (кэш мог содержать мусорные/устаревшие источники).
    force_source_refresh: bool = False
    # Политика источников (пер-студентная, префилл из профиля при создании сессии).
    # allow_any_sources=True → whitelist игнорируется при поиске.
    source_whitelist: List[str] = Field(default_factory=list)
    allow_any_sources: bool = True

    # --- RAG ---
    collection_id: Optional[str] = None
    # Граф знаний учебника (nodes/edges) + активная тема для подготовки по темам
    knowledge_graph: Optional[Dict[str, Any]] = None
    active_topic: Optional[str] = None
    awaiting_topic: bool = False  # после индексации ждём выбор темы/урока

    # --- Режим «урок» (объяснение темы перед квизом) ---
    lesson_text: Optional[str] = None
    lesson_done: bool = False      # урок по теме показан
    lesson_confirmed: bool = False # ученик подтвердил переход к квизу
    # «Дополнить материал»: найденные новые источники требуют перегенерации урока
    # (после поиска свежих материалов узел content_node пересобирает контент).
    lesson_rebuild: bool = False
    # Структурированный урок (LessonSchema): рендерится карточками вместо стены текста.
    # lesson_text — полный текст (render_text) для dedupe/resync/обратной совместимости.
    lesson_title: Optional[str] = None
    lesson_hook: Optional[str] = None
    lesson_definition: Optional[str] = None
    lesson_key_terms: List[Dict[str, str]] = Field(default_factory=list)
    lesson_diagram: Optional[Dict[str, Any]] = None   # LessonDiagram (dual-coding)
    lesson_sections: List[Dict[str, Any]] = Field(default_factory=list)
    lesson_summary: Optional[str] = None
    # Источники, использованные для генерации урока (URL, title, domain) —
    # атрибуция: ученик и проверяющий видят, из каких материалов собран урок.
    lesson_sources: List[Dict[str, str]] = Field(default_factory=list)
    # LessonEval: детерминированный судья-lite (0 LLM, не блокирует) + результат
    # фонового LLM-судьи groundedness (заполняется асинхронно, без задержки выдачи).
    lesson_eval: Optional[Dict[str, Any]] = None
    lesson_judge: Optional[Dict[str, Any]] = None

    # --- Тьюторинг (Ж-6) ---
    knowledge_map: Dict[str, float] = Field(default_factory=dict)
    # Тексты заданных вопросов (антидубликат 7.3.2): идут в промпт генерации и
    # сравниваются с новым вопросом по cosine-близости эмбеддингов
    asked_questions: List[str] = Field(default_factory=list)
    current_question: Optional[QuizCard] = None
    current_answers: List[str] = Field(default_factory=list)  # эталонные ответы LLM (не в UI)
    current_section: Optional[str] = None
    correct_count: int = 0
    answered_count: int = 0
    correct_streak: int = 0
    wrong_streak: int = 0
    total_llm_calls: int = 0
    # Лог вопросов сессии для экспорта учителю (вопрос→ответ→оценка→судья)
    records: List[Dict[str, Any]] = Field(default_factory=list)

    # --- Сессия ---
    session_status: Optional[Literal["active", "completed", "failed"]] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)

    # --- Исполнение графа (ввод/вывод между итерациями) ---
    pending_answer: Optional[str] = None       # ответ пользователя на текущий вопрос
    agent_options: Optional[List[str]] = None  # варианты ответа (для квиза)
    agent_message: Optional[str] = None        # сообщение пользователю (фидбек/суммари)
    quiz_complete: bool = False
    summary_text: Optional[str] = None
    last_judge_score: Optional[float] = None   # avg балл судьи по контракту «оценка» (для eval)
    # LinUCB contextual bandit (адаптивная модель ученика, src/adaptive.py):
    # JSON-безопасный dict {d, alpha, arms:[{A, b, n}]}. None — эвристика adjust_difficulty.
    bandit: Optional[Dict[str, Any]] = None

    # --- Scaffolding (адаптивное усложнение, roadmap) ---
    hint_level: int = 0
    attempts_on_question: int = 0
    retry_question_id: Optional[str] = None
    subtask_queue: Optional[List[str]] = None
    subtask_index: int = 0
    subtask_answer_ok: bool = False

    # --- Spaced repetition (SM-2 Question Bank, roadmap) ---
    review_requested: bool = False
    review_active: bool = False
    review_cards: List[Dict[str, Any]] = Field(default_factory=list)
    review_index: int = 0
    review_reviewed: int = 0
    review_correct: int = 0

    def update_knowledge(self, topic: str, score01: float) -> None:
        """Экспоненциальное сглаживание мастерства (Ж-6): 0.7*текущее + 0.3*результат."""
        current = self.knowledge_map.get(topic, 0.5)
        self.knowledge_map[topic] = round(0.7 * current + 0.3 * score01, 4)

    # --- Структурированный урок (LessonSchema) ---

    def set_lesson(self, lesson: Lesson) -> None:
        """Записать структурированный урок: полный текст + все поля для карточек.

        Детерминированный судья-lite (eval_lesson) вычисляется здесь же — это чистый
        Python без LLM, пользователь не ждёт ни миллисекунды.
        """
        self.lesson_text = lesson.render_text()
        self.lesson_title = lesson.title or None
        self.lesson_hook = lesson.hook or None
        self.lesson_definition = lesson.definition or None
        self.lesson_key_terms = [dict(t) for t in lesson.key_terms if isinstance(t, dict)]
        self.lesson_diagram = lesson.diagram.model_dump() if lesson.diagram else None
        self.lesson_sections = [s.model_dump() for s in lesson.sections]
        self.lesson_summary = lesson.summary or None
        from .lesson_eval import eval_lesson
        self.lesson_eval = eval_lesson(lesson, self.grade).to_dict()
        self.lesson_judge = None  # фоновый LLM-судья перезапускается для нового урока

    def set_plain_lesson(self, text: str) -> None:
        """Режимы explain/deep_dive: обычный текст без структуры (поля карточек очищаются)."""
        self.lesson_text = text
        self.lesson_title = None
        self.lesson_hook = None
        self.lesson_definition = None
        self.lesson_key_terms = []
        self.lesson_diagram = None
        self.lesson_sections = []
        self.lesson_summary = None
        self.lesson_sources = []
        self.lesson_eval = None
        self.lesson_judge = None

    def clear_lesson(self) -> None:
        self.lesson_text = None
        self.lesson_title = None
        self.lesson_hook = None
        self.lesson_definition = None
        self.lesson_key_terms = []
        self.lesson_diagram = None
        self.lesson_sections = []
        self.lesson_summary = None
        self.lesson_sources = []
        self.lesson_eval = None
        self.lesson_judge = None
        self.lesson_done = False

    def lesson_payload(self, topic: str = "") -> Dict[str, Any]:
        """Полезная нагрузка события tutor.lesson / MessageResponse для фронтенда."""
        return {
            "text": self.lesson_text or "",
            "topic": topic or self.lesson_title or self.topic or "",
            "sources": self.lesson_sources,
            "lesson": {
                "title": self.lesson_title,
                "hook": self.lesson_hook,
                "definition": self.lesson_definition,
                "key_terms": self.lesson_key_terms,
                "diagram": self.lesson_diagram,
                "sections": self.lesson_sections,
                "summary": self.lesson_summary,
                "eval": self.lesson_eval,
            },
        }
