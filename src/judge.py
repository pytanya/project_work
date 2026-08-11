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

    if judge_call is None:
        client = LLMClient(role=ROLE_JUDGE)
        judge_call = lambda msgs: client.chat(msgs, temperature=0.0, max_tokens=200).content or ""

    raw = judge_call(_build_prompt(contract, criteria, subject))
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
