"""
EduTutor — LLM-as-Judge (раздел 4.2.3, К-4).

Три контракта судьи (модель другого семейства — Gemini на RouterAI, без VPN):
1. judge_question     — качество вопроса квиза (relevance, grade_fit, clarity, factual_ok);
2. judge_explanation  — качество объяснения (accuracy, comprehensibility, citation_ok);
3. judge_evaluation   — качество оценки ответа ученика (grade_correct, feedback_ok, difficulty_fit).

Возвращает баллы 0..10 по каждому критерию + вердикт (≥7 — pass).
JSON-парсинг с fallback — из tutor.parse_llm_json.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import ROLE_JUDGE
from .tutor import grade_prompt, parse_llm_json

logger = logging.getLogger("edututor.judge")

PASS_THRESHOLD = 7.0

QUESTION_CRITERIA = ["relevance", "grade_fit", "clarity", "factual_ok"]
EXPLANATION_CRITERIA = ["accuracy", "comprehensibility", "citation_ok"]
EVALUATION_CRITERIA = ["grade_correct", "feedback_ok", "difficulty_fit"]
LESSON_CRITERIA = ["groundedness", "coherence", "grade_fit", "no_contradiction"]
QUIZ_QUESTION_CRITERIA = ["answerable", "unambiguous", "difficulty_fit", "factual_ok", "clarity"]


@dataclass
class JudgeResult:
    contract: str
    criteria: Dict[str, float] = field(default_factory=dict)
    avg_score: float = 0.0
    verdict: str = "fail"
    raw: str = ""


def _build_prompt(contract: str, criteria: List[str], subject: Dict[str, Any]) -> List[Dict[str, str]]:
    system = (
        "Ты — независимый судья качества в образовательном агенте EduTutor "
        "(модель другого семейства, не та, что генерирует контент). "
        f"Оцени объект по критериям 0..10: {', '.join(criteria)}. "
        'Верни строго JSON: {"criteria": {"<критерий>": <0..10>, ...}, "comment": "..."}.'
    )
    parts = [f"{k}: {v}" for k, v in subject.items()]
    user = "\n".join(parts)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _judge(
    contract: str,
    criteria: List[str],
    subject: Dict[str, Any],
    judge_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> JudgeResult:
    from .llm_client import LLMClient

    prompt = _build_prompt(contract, criteria, subject)
    if judge_call is None:
        client = LLMClient(role=ROLE_JUDGE)
        try:
            raw = client.chat(prompt, temperature=0.0, max_tokens=200).content or ""
        except Exception as exc:
            # Офлайн/недоступен LLM: нейтральный вердикт, не роняем поток
            # (судья — не блокирующий слой, см. section 4.2.3).
            logger.warning("Судья %s: LLM недоступен (%s) — нейтральный вердикт", contract, exc)
            neutral = {c: 5.0 for c in criteria}
            return JudgeResult(contract=contract, criteria=neutral, avg_score=5.0,
                               verdict="fail", raw="")
    else:
        raw = judge_call(prompt)
    data = parse_llm_json(raw)
    raw_criteria = data.get("criteria") if isinstance(data.get("criteria"), dict) else {}

    scores: Dict[str, float] = {}
    for c in criteria:
        try:
            v = float(raw_criteria.get(c, 0.0))
        except (TypeError, ValueError):
            v = 0.0
        scores[c] = max(0.0, min(10.0, v))

    avg = round(sum(scores.values()) / len(scores), 2) if scores else 0.0
    return JudgeResult(
        contract=contract,
        criteria=scores,
        avg_score=avg,
        verdict="pass" if avg >= PASS_THRESHOLD else "fail",
        raw=raw,
    )


def judge_question(
    question: str,
    topic: str,
    grade: Optional[str],
    judge_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> JudgeResult:
    """Контракт «вопрос»: relevance, grade_fit, clarity, factual_ok."""
    return _judge(
        "question",
        QUESTION_CRITERIA,
        {
            "question": question,
            "topic": topic,
            "grade": grade or "не указан",
            "grade_guidance": grade_prompt(grade),
        },
        judge_call=judge_call,
    )


def judge_quiz_question(
    question: str,
    topic: str,
    grade: Optional[str],
    answer_type: Optional[str] = None,
    options: Optional[List[str]] = None,
    correct_answers: Optional[List[str]] = None,
    difficulty: Optional[str] = None,
    judge_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> JudgeResult:
    """Контракт «вопрос квиза»: answerable, unambiguous, difficulty_fit, factual_ok, clarity.

    Оценивает качество вопроса с точки зрения обучающего квиза:
    - answerable — по вопросу/вариантам можно однозначно ответить, используя контекст;
    - unambiguous — для single ровно один верный вариант, дистракторы правдоподобны;
    - difficulty_fit — сложность соответствует заявленной (easy/medium/hard);
    - factual_ok — факты в вопросе и вариантах соответствуют контексту;
    - clarity — формулировка ясная, без двойного отрицания и ловушек.
    """
    return _judge(
        "quiz_question",
        QUIZ_QUESTION_CRITERIA,
        {
            "question": question,
            "topic": topic,
            "grade": grade or "не указан",
            "answer_type": answer_type or "open",
            "options": options or [],
            "correct_answers": correct_answers or [],
            "difficulty": difficulty or "medium",
            "grade_guidance": grade_prompt(grade),
            "instructions": (
                "Оцени вопрос учебного квиза: answerable — по нему можно дать "
                "однозначный ответ по материалу; unambiguous — для single ровно один "
                "верный вариант, дистракторы правдоподобны и не перекрываются; "
                "difficulty_fit — сложность соответствует easy/medium/hard; "
                "factual_ok — вопрос и варианты не содержат фактических ошибок; "
                "clarity — нет двойного отрицания, двусмысленности, ловушек."
            ),
        },
        judge_call=judge_call,
    )


def judge_explanation(
    explanation: str,
    citation: Dict[str, Any],
    grade: Optional[str],
    judge_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> JudgeResult:
    """Контракт «объяснение»: accuracy, comprehensibility, citation_ok."""
    return _judge(
        "explanation",
        EXPLANATION_CRITERIA,
        {
            "explanation": explanation,
            "citation": citation,
            "grade": grade or "не указан",
        },
        judge_call=judge_call,
    )


def judge_evaluation(
    question: str,
    answer: str,
    graded: Dict[str, Any],
    judge_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> JudgeResult:
    """Контракт «оценка ответа ученика»: grade_correct, feedback_ok, difficulty_fit."""
    return _judge(
        "evaluation",
        EVALUATION_CRITERIA,
        {
            "question": question,
            "student_answer": answer,
            "graded_score": graded.get("score", 0.0),
            "graded_correct": graded.get("correct", False),
            "feedback": graded.get("feedback", ""),
        },
        judge_call=judge_call,
    )


def _citation_groundedness_cap(citations01: Any) -> Optional[float]:
    """Потолок groundedness по детерминированной оценке цитат (0..1 из eval_lesson).

    Урок без цитат (§N/источник) не может быть «подтверждён контекстом»:
    groundedness ≥ 7 при цитатах 0 невозможно — это и был скрытый дефект
    («цитаты: 0/10» при «groundedness: 7/10»).
    """
    try:
        v = float(citations01)
    except (TypeError, ValueError):
        return None
    if v >= 1.0:
        return None
    # 0 цитат → groundedness ≈ 1 (урок не демонстрирует опору на источник → fail
    # даже при высоких прочих критериях); 1.0 → без капа.
    return round(max(1.0, v * 10.0), 1)


def judge_lesson(
    lesson_text: str,
    context: List[str],
    grade: Optional[str],
    judge_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    eval_criteria: Optional[Dict[str, float]] = None,
) -> JudgeResult:
    """Контракт «урок» (LLM, фоновый): groundedness, coherence, grade_fit, no_contradiction.

    eval_criteria — детерминированные критерии eval_lesson (lesson_eval.criteria):
    groundedness жёстко ограничивается сверху цитатами, чтобы судья не «прощал»
    урок без ссылок на источник.

    Запускается ТОЛЬКО асинхронно (api/engine.py) после выдачи урока ученику —
    никогда не блокирует переход к квизу.
    """
    ctx = "\n---\n".join(context)[:6000]
    result = _judge(
        "lesson",
        LESSON_CRITERIA,
        {
            "lesson": lesson_text[:4000],
            "context": ctx or "контекст пуст",
            "grade": grade or "не указан",
            "grade_guidance": grade_prompt(grade),
            "auto_eval": eval_criteria or {},
            "instructions": (
                "Оцени: groundedness — каждый факт урока подтверждён контекстом; "
                "coherence — связность структуры; grade_fit — сложность соответствует классу; "
                "no_contradiction — в уроке и его схеме нет фактов, противоречащих "
                "контексту или друг другу. auto_eval.citations — доля секций с цитатой "
                "(§N/источник): чем меньше цитат, тем ниже groundedness — урок обязан "
                "демонстрировать опору на источник."
            ),
        },
        judge_call=judge_call,
    )
    if eval_criteria:
        cap = _citation_groundedness_cap(eval_criteria.get("citations"))
        if cap is not None and result.criteria.get("groundedness", 0.0) > cap:
            result.criteria["groundedness"] = cap
            result.avg_score = round(sum(result.criteria.values()) / len(result.criteria), 2)
            result.verdict = "pass" if result.avg_score >= PASS_THRESHOLD else "fail"
    return result
