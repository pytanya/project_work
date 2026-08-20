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
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from api.schemas import QuizCard
from .states import TutorState

logger = logging.getLogger("edututor.tutor")

MAX_EXPLANATION_CHARS = 2500

# Ключевые термины берём из контекста вопроса для пре-оценки (В-2)
# PRE_CHECK_MIN_LENGTH — определён ниже, рядом с simplicity_precheck()


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


def generate_text(
    messages: List[Dict[str, str]],
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
    role: str = "tutor",
    temperature: float = 0.3,
    max_tokens: Optional[int] = 512,
) -> str:
    """Вызов LLM: реальный стриминг токенов (on_token) или обычный вызов.

    - llm_call задан (мок в тестах) → обычный вызов без стриминга;
    - on_token задан → LLMClient.chat_stream(stream=True), токены уходят в браузер;
    - иначе → обычный LLMClient.chat.
    """
    if llm_call is not None:
        return llm_call(messages)
    from .llm_client import LLMClient

    client = LLMClient(role=role)
    if on_token is not None:
        resp = client.chat_stream(messages, on_chunk=on_token,
                                  temperature=temperature, max_tokens=max_tokens)
        return resp.content or ""
    return client.chat(messages, temperature=temperature, max_tokens=max_tokens).content or ""


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
    asked: Optional[List[str]] = None,
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
            "Для single — ровно 1 правильный вариант, для multiple — все правильные. "
            "ВАЖНО: варианты-дистракторы делай правдоподобными — они должны быть похожи "
            "на правильный по теме/форме, но неверны по смыслу (никакой очевидной абсурдности, "
            "одинаковой длины и стиля с правильным)."
        )
    )
    if asked:
        system += (
            " Уже задавали такие вопросы: "
            + "; ".join(str(q) for q in asked[-10:])
            + ". НЕ повторяй их по смыслу — задай другой вопрос по тому же материалу."
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
        topic, context, difficulty, state.grade, state.curriculum, simple=simple,
        asked=list(state.asked_questions),
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
    state.asked_questions.append(card.question)  # тексты вопросов — для антидубликата (7.3.2)
    state.current_question = card
    return card


def is_duplicate_question(
    embedder: Any,
    new_question: str,
    prev_questions: List[str],
    threshold: float = 0.85,
) -> bool:
    """Семантический антидубликат (спека 7.3.2): cosine-близость нового вопроса
    к любому из уже заданных ≥ threshold → дубль (True).

    При недоступности эмбеддера или вырожденных векторах возвращает False
    (не блокируем генерацию вопроса).
    """
    if not prev_questions or not (new_question or "").strip():
        return False
    try:
        new_vec = embedder.encode_query(new_question)
        prev_vecs = embedder.encode(list(prev_questions))
    except Exception:
        return False

    def _cos(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    return any(_cos(new_vec, pv) >= threshold for pv in prev_vecs)


# ----------------------------------------------------------------------
# Урок: объяснение темы перед квизом (режим lesson)
# ----------------------------------------------------------------------
def _lesson_prompt(topic: str, context: List[str], grade: Optional[str], curriculum: Optional[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor. Составь короткий понятный УРОК по теме ученика "
        "только на основе контекста учебника. "
        + grade_prompt(grade)
        + (
            f" Учебная программа: {curriculum}." if curriculum else ""
        )
        + (
            " Структура: 3-5 абзацев — что это такое, главные факты, пример, "
            "итог одним предложением. Пиши своими словами, связно, без списков-перечислений "
            "из канцелярита, без заголовков-эмодзи. Не выдумывай факты за пределами контекста. "
            "Отвечай ЧИСТЫМ ТЕКСТОМ урока — без JSON, без кавычек-обёрток, без форматирования."
        )
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст учебника:\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_lesson(
    topic: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> str:
    """Синтез урока по RAG-контексту (тьютор-модель). on_token — стриминг токенов в браузер."""
    messages = _lesson_prompt(topic, context, state.grade, state.curriculum)
    raw = generate_text(messages, llm_call=llm_call, on_token=on_token,
                        role="tutor", temperature=0.4, max_tokens=700)
    data = parse_llm_json(raw)
    text = str(data.get("text") or raw or "").strip()
    if len(text) < 40:
        # Fallback: даём первый абзац контекста как урок
        text = (context[0] if context else f"Материалы по теме «{topic}» ещё пополняются.")[:1200]
    return text


# ----------------------------------------------------------------------
# Объяснение темы (режим explain) и глубокий разбор (режим deep_dive)
# ----------------------------------------------------------------------
def _topic_explain_prompt(topic: str, context: List[str], grade: Optional[str], curriculum: Optional[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor. Объясни тему ученику понятным языком по контексту учебника. "
        + grade_prompt(grade)
        + (
            f" Учебная программа: {curriculum}." if curriculum else ""
        )
        + (
            " Структура: что это такое (определение), почему это важно, главные факты, "
            "наглядный пример, итог одним предложением. Пиши связно, без списков-канцелярита "
            "и заголовков-эмодзи. Не выдумывай факты за пределами контекста. "
            "Отвечай ЧИСТЫМ ТЕКСТОМ объяснения — без JSON и обёрток."
        )
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст учебника:\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_explanation(
    topic: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> str:
    """Объяснение темы (режим explain): определение, факты, пример, цитата."""
    messages = _topic_explain_prompt(topic, context, state.grade, state.curriculum)
    raw = generate_text(messages, llm_call=llm_call, on_token=on_token,
                        role="tutor", temperature=0.3, max_tokens=700)
    data = parse_llm_json(raw)
    text = str(data.get("text") or raw or "").strip()
    if len(text) < 40:
        text = (context[0] if context else f"Материалы по теме «{topic}» ещё пополняются.")[:1200]
    return text


def _deep_dive_prompt(topic: str, context: List[str], grade: Optional[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — эксперт EduTutor, делаешь ГЛУБОКИЙ РАЗБОР темы по нескольким фрагментам учебника. "
        + grade_prompt(grade)
        + (
            " Структура: ключевые понятия и их определения, внутренние связи между разделами, "
            "причинно-следственные цепочки, примеры, типичные ошибки, вывод. Опирайся ТОЛЬКО "
            "на предоставленный контекст, не выдумывай. Указывай параграфы-источники (§N) для "
            "ключевых утверждений. "
            "Отвечай ЧИСТЫМ ТЕКСТОМ разбора — без JSON и обёрток."
        )
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст (несколько разделов):\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_deep_dive(
    topic: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> str:
    """Глубокий разбор (режим deep_dive): multi-chunk синтез эксперт-моделью."""
    messages = _deep_dive_prompt(topic, context, state.grade)
    raw = generate_text(messages, llm_call=llm_call, on_token=on_token,
                        role="expert", temperature=0.2, max_tokens=900)
    data = parse_llm_json(raw)
    text = str(data.get("text") or raw or "").strip()
    if len(text) < 40:
        text = (context[0] if context else f"Материалы по теме «{topic}» ещё пополняются.")[:1500]
    return text


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
    # Сверка с эталоном детерминирована (эталон LLM предгенерён) — LLM не зовём:
    # совпало → верно, не совпало → неверно с показом правильного варианта.
    if is_closed:
        if refs:
            if _ref_match(answer, refs):
                return GradedAnswer(
                    score=1.0, correct=True, feedback="Верно!", citation_ok=True,
                    model_used="reference", precheck_passed=True,
                )
            return GradedAnswer(
                score=0.0, correct=False,
                feedback=f"Неверно. Правильный ответ: {', '.join(refs)}.",
                citation_ok=True, model_used="reference", precheck_passed=True,
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
