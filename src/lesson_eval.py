"""
EduTutor — LessonEval: оценка качества урока (суммаризация + судья-lite).

ДВА уровня, чтобы судья НЕ был задержкой:

1. eval_lesson(lesson, grade) — детерминированный судья-lite, чистая Python
   (0 LLM-вызовов, микросекунды). Считает 5 критериев 0..1:
   structure      — полнота структуры (hook/definition/секции/итог/термины);
   citations      — доля секций с цитатой §N/источником (сигнал groundedness);
   diagram        — согласованность схемы с текстом урока (подписи не вводят
                    новых понятий — нет противоречий с секциями);
   readability    — средняя длина предложения в пределах бюджета класса;
   length         — объём урока в пределах бюджета класса.
   Выполняется синхронно в set_lesson — пользователь не ждёт ни миллисекунды.

2. judge_lesson(...) в src/judge.py — LLM-судья groundedness (контракт lesson).
   Запускается ТОЛЬКО в фоне (api/engine.py) после выдачи урока — никогда не
   блокирует «Готов(а) перейти к квизу?».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from api.schemas import Lesson

PASS_THRESHOLD = 0.7

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+")
_SENT_SPLIT_RE = re.compile(r"[.!?…]+")

# Бюджеты по классам (согласованы с grade_prompt / difficulty_for_grade):
# чем младше — тем короче предложения и меньше объём.
_GRADE_PARAMS = {
    "school": {"max_words_sentence": 12, "max_chars": 2200},
    "middle": {"max_words_sentence": 18, "max_chars": 3200},
    "high": {"max_words_sentence": 24, "max_chars": 4000},
    "student": {"max_words_sentence": 26, "max_chars": 4200},
}


def _grade_key(grade: Optional[str]) -> str:
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return "student"
    if g <= 6:
        return "school"
    if g <= 9:
        return "middle"
    if g <= 11:
        return "high"
    return "student"


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _score_structure(lesson: Lesson) -> float:
    s = 0.0
    if lesson.hook:
        s += 0.2
    if lesson.definition:
        s += 0.2
    n = len(lesson.sections)
    if 2 <= n <= 4:
        s += 0.25
    elif n == 1:
        s += 0.15
    elif n >= 5:
        s += 0.1
    if lesson.summary:
        s += 0.2
    if lesson.key_terms:
        s += 0.15
    return round(s, 3)


def _score_citations(lesson: Lesson) -> float:
    if not lesson.sections:
        return 0.0
    with_cit = sum(1 for s in lesson.sections if (s.citation or "").strip())
    return round(with_cit / len(lesson.sections), 3)


def _score_diagram(lesson: Lesson) -> float:
    """Согласованность схемы с текстом: подписи узлов встречаются в уроке/терминах.

    Прокси для проверки «схема не противоречит тексту» без LLM: если диаграмма
    вводит понятие, отсутствующее в секциях и словарике, — считается несогласованной.
    Отсутствие схемы (0.5) — нейтрально: урок без диаграммы не проваливается,
    но и не получает плюс за dual-coding.
    """
    d = lesson.diagram
    if not d or not d.nodes:
        return 0.5
    text_tokens = _tokens(lesson.render_text())
    term_tokens = set()
    for t in lesson.key_terms:
        term_tokens |= _tokens(str(t.get("term", "")))
    haystack = text_tokens | term_tokens
    covered = 0
    total = 0
    for n in d.nodes:
        nt = _tokens(n.label)
        if not nt:
            continue
        total += 1
        if nt & haystack:
            covered += 1
    label_cov = covered / total if total else 0.0
    ids = {n.id for n in d.nodes}
    edges_ok = all(
        e.source in ids and e.target in ids
        for e in d.edges
    ) if d.edges else True
    return round(0.6 * min(1.0, label_cov) + 0.4 * (1.0 if edges_ok else 0.5), 3)


def _score_readability(lesson: Lesson, grade: Optional[str]) -> float:
    params = _GRADE_PARAMS[_grade_key(grade)]
    bodies = " ".join(s.body for s in lesson.sections if s.body)
    sentences = [s for s in _SENT_SPLIT_RE.split(bodies) if s.strip()]
    if not sentences:
        return 0.5
    words_per_sentence = [len(_TOKEN_RE.findall(s)) for s in sentences]
    avg = sum(words_per_sentence) / len(words_per_sentence)
    if avg <= params["max_words_sentence"]:
        return 1.0
    # линейное падение: до 2x бюджета — от 1 до 0
    return round(max(0.0, 1.0 - (avg - params["max_words_sentence"]) / params["max_words_sentence"]), 3)


def _score_length(lesson: Lesson, grade: Optional[str]) -> float:
    params = _GRADE_PARAMS[_grade_key(grade)]
    total = len(lesson.render_text())
    if total <= params["max_chars"]:
        return 1.0
    over = (total - params["max_chars"]) / params["max_chars"]
    return round(max(0.0, 1.0 - over), 3)


@dataclass
class LessonEvalResult:
    criteria: Dict[str, float] = field(default_factory=dict)
    avg_score: float = 0.0
    verdict: str = "fail"
    grade_budget: str = "student"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criteria": self.criteria,
            "avg_score": self.avg_score,
            "verdict": self.verdict,
            "grade_budget": self.grade_budget,
        }


def eval_lesson(lesson: Lesson, grade: Optional[str] = None) -> LessonEvalResult:
    """Детерминированная оценка урока (0 LLM-вызовов — не добавляет задержки)."""
    criteria = {
        "structure": _score_structure(lesson),
        "citations": _score_citations(lesson),
        "diagram": _score_diagram(lesson),
        "readability": _score_readability(lesson, grade),
        "length": _score_length(lesson, grade),
    }
    avg = round(sum(criteria.values()) / len(criteria), 3)
    return LessonEvalResult(
        criteria=criteria,
        avg_score=avg,
        verdict="pass" if avg >= PASS_THRESHOLD else "fail",
        grade_budget=_grade_key(grade),
    )
