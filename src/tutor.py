"""
EduTutor — тьюторинг-цикл (раздел 7).

- grade_prompt(grade): параметризация «понятного языка» по классам (Ж-3, 7.1).
- generate_question: RAG-контекст → QuizCard (дешёвая модель — простые вопросы,
  TUTOR_MODEL — сложные; 7.1).
- simplicity_precheck: rule-based пре-оценка ответа (В-2: длина/ключевые термины).
- evaluate_answer: пре-оценка → финальная оценка (TUTOR основной поток, EXPERT —
  сложные/нестандартные ответы, критерий Ж-8).
- adjust_difficulty: ↑ при 3+ правильных подряд, ↓ при 2+ ошибках.
- explain_error: объяснение с цитатой §N.
- anti-repeat: история заданных вопросов (13.2).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from api.schemas import QuizCard
from .states import TutorState

logger = logging.getLogger("edututor.tutor")

MAX_EXPLANATION_CHARS = 2500

# Ключевые термины берём из контекста вопроса для пре-оценки (В-2)
PRE_CHECK_MIN_LENGTH = 20


# ----------------------------------------------------------------------
# JSON-парсинг ответа LLM (с fallback)
# ----------------------------------------------------------------------
def parse_llm_json(text: str) -> Dict[str, Any]:
    """Извлечение JSON из ответа LLM (возможен текст вокруг / ```json ```)."""
    text = (text or "").strip()
    if not text:
        return {}
    # Убираем fenced code block
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1).strip()
    # Ищем первую { ... } (или [ ... ])
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _score01(value: Any) -> float:
    """Приводит оценку LLM (0..10 или 0..1) к 0..1."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    if v > 1.0:
        return max(0.0, min(1.0, v / 10.0))
    return max(0.0, min(1.0, v))


# ----------------------------------------------------------------------
# grade_prompt (Ж-3)
# ----------------------------------------------------------------------
def grade_prompt(grade: Optional[str]) -> str:
    """Фрагмент system-промпта по классу (таблица 7.1)."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        g = 0
    if g and g <= 6:
        return (
            "Обучаемый — ученик 5-6 класса. Используй простые слова, короткие "
            "предложения, без абстрактных терминов. Вопросы — на один факт/шаг."
        )
    if g and g <= 9:
        return (
            "Обучаемый — ученик 7-9 класса. Допустимы термины из учебника, "
            "вопросы на понимание (2 шага)."
        )
    if g and g <= 11:
        return (
            "Обучаемый — ученик 10-11 класса. Допустимы анализ и синтез, "
            "вопросы multiple-correct и причинно-следственные."
        )
    return "Обучаемый — студент. Уровень — высшее образование, допустима терминология."


def difficulty_for_grade(grade: Optional[str]) -> str:
    """Стартовая сложность по классу (easy 5-6, medium 7-9, hard 10-11)."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return "medium"
    if g and g <= 6:
        return "easy"
    if g and g <= 9:
        return "medium"
    return "hard"


# ----------------------------------------------------------------------
# Генерация вопроса
# ----------------------------------------------------------------------
def _question_prompt(
    topic: str,
    context: List[str],
    difficulty: str,
    grade: Optional[str],
    curriculum: Optional[str],
    simple: bool,
) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor, генерируешь вопрос учебного квиза. "
        + grade_prompt(grade)
        + (
            " Сгенерируй ПРОСТОЙ фактологический вопрос по контексту."
            if simple
            else " Сгенерируй вопрос на понимание/применение."
        )
        + (
            f" Сложность: {difficulty}. Учебная программа: {curriculum}."
            if curriculum
            else f" Сложность: {difficulty}."
        )
        + (
            " Верни строго JSON: {\"question\": \"...\", \"options\": [\"...\"] или null, "
            "\"answer_type\": \"single\"|\"multiple\"|\"open\", \"topic\": \"<тема>\", "
            "\"correct_answers\": [\"правильный вариант/модельный ответ\"]}. "
            "Для open-вопроса options=null, correct_answers = [\"эталонный ответ\"]. "
            "Для single — ровно 1 правильный вариант, для multiple — все правильные."
        )
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст:\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_question(
    topic: str,
    context: List[str],
    difficulty: str,
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    question_id: Optional[str] = None,
) -> QuizCard:
    """Генерация вопроса по RAG-контексту (дешёвая/тьютор-модель решается вызывающим)."""
    from .llm_client import LLMClient

    if llm_call is None:
        client = LLMClient(role="tutor")
        llm_call = lambda msgs: client.chat(msgs, temperature=0.3, max_tokens=512).content or ""

    simple = difficulty == "easy"
    messages = _question_prompt(
        topic, context, difficulty, state.grade, state.curriculum, simple=simple
    )
    raw = llm_call(messages)
    data = parse_llm_json(raw)
    if not data or not data.get("question"):
        # Fallback: шаблонный вопрос из контекста
        snippet = (context[0] if context else topic)[:120]
        data = {
            "question": f"Что говорится в материале о «{topic}»?",
            "options": None,
            "answer_type": "open",
            "topic": topic,
        }
    qid = question_id or f"q{len(state.asked_questions) + 1}"
    card = QuizCard(
        question_id=qid,
        question=str(data.get("question", "")).strip(),
        options=data.get("options") if isinstance(data.get("options"), list) else None,
        answer_type=data.get("answer_type") if data.get("answer_type") in ("single", "multiple", "open") else "open",
        difficulty=difficulty,
        topic=str(data.get("topic") or topic),
    )
    # Эталонные ответы генерирует LLM (мозг); они НЕ входят в QuizCard/UI.
    refs = data.get("correct_answers")
    state.current_answers = [str(r).strip() for r in refs if str(r).strip()] if isinstance(refs, list) else []
    state.asked_questions.append(qid)
    state.current_question = card
    return card


# ----------------------------------------------------------------------
# Оценка ответа (В-2, Ж-8)
# ----------------------------------------------------------------------
PRE_CHECK_MIN_LENGTH = 15
PRE_CHECK_MIN_WORDS = 3


def simplicity_precheck(answer: str, context: List[str]) -> bool:
    """Rule-based judge-lite (В-2): отсекает ТОЛЬКО пустые/слишком короткие ответы.

    Важно: НЕ требуем совпадения ключевых слов с чанком — ученик может ответить
    своими словами (парафраз), и это корректный ответ. Смысл оценивает LLM
    (evaluate_answer). Здесь — лишь «не пусто и не мусор».
    """
    text = (answer or "").strip()
    if len(text) < PRE_CHECK_MIN_LENGTH:
        return False
    words = re.findall(r"[а-яёa-z]{2,}", text.lower())
    if len(words) < PRE_CHECK_MIN_WORDS:
        return False
    return True


def _decide_eval_model(state: TutorState, answer: str) -> str:
    """Критерий Ж-8: эксперт — для сложных/нестандартных ответов, иначе тьютор."""
    text = (answer or "").strip()
    # 2.1 неструктурированный/развёрнутый (длинный свободный текст)
    if len(text) > 600:
        return "expert"
    # 2.3 повторные ошибки по knowledge_map
    topic = state.current_question.topic if state.current_question else ""
    if topic and state.knowledge_map.get(topic, 0.5) < 0.35:
        return "expert"
    return "tutor"


def _eval_prompt(
    question: str,
    answer: str,
    context: List[str],
    correct_answers: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    system = (
        "Ты — строгий экзаменатор EduTutor. Оцени ответ ученика по эталону из контекста. "
        "Верни строго JSON: {\"score\": <0..10>, \"correct\": true|false, "
        "\"feedback\": \"краткое пояснение ошибки\", \"citation_ok\": true|false}."
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    refs = ", ".join(correct_answers) if correct_answers else ""
    user = f"Вопрос: {question}\nОтвет ученика: {answer}\n"
    if refs:
        user += f"Правильный(е) ответ(ы): {refs}\n"
    user += f"Эталон (контекст):\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _ref_match(answer: str, refs: List[str]) -> bool:
    """Сверка ответа ученика с эталонными ответами (нормализация без учёта регистра/пробелов)."""
    def norm(s: str) -> str:
        return " ".join(str(s).lower().split())

    a = norm(answer)
    for r in refs:
        rn = norm(r)
        if not rn:
            continue
        if a == rn:
            return True
        # для длинных эталонов допускаем вхождение (короткий в длинном и наоборот)
        if len(rn) >= 4 and (rn in a or a in rn):
            return True
    return False


@dataclass
class GradedAnswer:
    score: float  # 0..1
    correct: bool
    feedback: str
    citation_ok: bool
    model_used: str
    precheck_passed: bool


def evaluate_answer(
    question: str,
    answer: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> GradedAnswer:
    """Полная оценка ответа: пре-оценка (В-2) → финальная оценка (Ж-8)."""
    from .llm_client import LLMClient

    # Эталонные ответы, сгенерированные LLM при создании вопроса (не из UI)
    refs = list(state.current_answers)
    q = state.current_question
    is_closed = bool(q and q.answer_type in ("single", "multiple") and q.options)

    # Закрытый вопрос: ответ = выбранный вариант, пре-проверка длины не нужна.
    # Сначала — детерминированная сверка с эталоном.
    if is_closed:
        if refs and _ref_match(answer, refs):
            return GradedAnswer(
                score=1.0, correct=True, feedback="Верно!", citation_ok=True,
                model_used="reference", precheck_passed=True,
            )
    else:
        precheck = simplicity_precheck(answer, context)
        if not precheck:
            return GradedAnswer(
                score=0.0, correct=False,
                feedback="Ответ слишком короткий — уточните, пожалуйста.",
                citation_ok=False, model_used="rule-based", precheck_passed=False,
            )

    role = _decide_eval_model(state, answer)  # Ж-8
    if llm_call is None:
        client = LLMClient(role=role)
        llm_call = lambda msgs: client.chat(msgs, temperature=0.0, max_tokens=300).content or ""

    raw = llm_call(_eval_prompt(question, answer, context, correct_answers=refs or None))
    data = parse_llm_json(raw)
    score01 = _score01(data.get("score", 5.0))
    correct = bool(data.get("correct", score01 >= 0.7))
    return GradedAnswer(
        score=score01,
        correct=correct,
        feedback=str(data.get("feedback", "") or ""),
        citation_ok=bool(data.get("citation_ok", False)),
        model_used=role,
        precheck_passed=True,
    )


# ----------------------------------------------------------------------
# Адаптация сложности (7.1)
# ----------------------------------------------------------------------
def adjust_difficulty(state: TutorState, correct: bool) -> str:
    """↑ при 3+ правильных подряд, ↓ при 2+ ошибках подряд (7.1)."""
    order = ["easy", "medium", "hard"]
    cur = state.difficulty if state.difficulty in order else "medium"
    idx = order.index(cur)

    state.answered_count += 1
    if correct:
        state.correct_count += 1
        state.correct_streak += 1
        state.wrong_streak = 0
        if state.correct_streak >= 3 and idx < len(order) - 1:
            state.correct_streak = 0
            state.difficulty = order[idx + 1]
    else:
        state.wrong_streak += 1
        state.correct_streak = 0
        if state.wrong_streak >= 2 and idx > 0:
            state.wrong_streak = 0
            state.difficulty = order[idx - 1]
    return state.difficulty


def update_knowledge_map(state: TutorState, topic: str, score01: float) -> None:
    """Экспоненциальное сглаживание (Ж-6): 0.7*текущее + 0.3*результат."""
    state.update_knowledge(topic, score01)


# ----------------------------------------------------------------------
# Объяснение ошибки (с цитатой §N)
# ----------------------------------------------------------------------
def _explain_prompt(question: str, answer: str, correct_answer: Optional[str], context: List[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor. Объясни ученику его ошибку доступно для его класса. "
        "Обязательно приведи цитату из учебника с номером параграфа (§N) из контекста. "
        "Верни строго JSON: {\"text\": \"объяснение\", \"citation\": {\"paragraph\": \"§12\", \"source\": \"...\"}}."
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Вопрос: {question}\nОтвет ученика: {answer}\nКонтекст (учебник):\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def explain_error(
    question: str,
    answer: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> Dict[str, Any]:
    """Объяснение ошибки с цитатой (EXPERT_MODEL — deep dive, раздел 4.2.2)."""
    from .llm_client import LLMClient

    if llm_call is None:
        client = LLMClient(role="expert")
        llm_call = lambda msgs: client.chat(msgs, temperature=0.2, max_tokens=500).content or ""

    raw = llm_call(_explain_prompt(question, answer, None, context))
    data = parse_llm_json(raw)
    citation = data.get("citation") if isinstance(data.get("citation"), dict) else {}
    return {
        "text": str(data.get("text", "") or "Разберём ошибку подробнее."),
        "citation": {
            "paragraph": citation.get("paragraph", ""),
            "source": citation.get("source", ""),
        },
    }
