"""EduTutor — scaffolding: лестница подсказок (адаптивное усложнение).

Уровень 1 — наводящая подсказка (ключевой термин/направление, ответ не раскрывается).
Уровень 2 — раскрывающая («начни так: …», первые слова эталонного ответа).
LLM-опция: если передан llm_call — используем его для более мягкой формулировки;
иначе (и при сбое) — детерминированный rule-based fallback (никакой LLM).
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

logger = logging.getLogger("edututor.scaffold")

_STOP = {"правильный", "ответ", "правильно", "ответить", "начни", "начать", "это", "вопрос"}


def _keywords(text: str, limit: int = 3) -> List[str]:
    words = re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", text or "")
    seen, out = set(), []
    for w in words:
        low = w.lower()
        if low not in seen and low not in _STOP:
            seen.add(low)
            out.append(w)
        if len(out) >= limit:
            break
    return out


def hint_for(
    question: str,
    correct_answer: str,
    context: List[str],
    level: int,
    llm_call: Optional[object] = None,
) -> str:
    """Подсказка уровня level (1 или 2). Rule-based по умолчанию; llm_call — опция."""
    level = 1 if level < 1 else (2 if level > 2 else level)
    ca = (correct_answer or "").strip()
    ctx = " ".join(context or []).strip()

    if llm_call is not None:
        try:
            role = "Совет" if level == 1 else "Начни"
            prompt = [
                {"role": "system", "content": "Ты — тьютор. Дай ОДНУ короткую подсказку "
                 f"({role}) для вопроса, НЕ выдавая полный ответ. 1-2 предложения."},
                {"role": "user", "content": f"Вопрос: {question}\nЭталон: {ca[:200]}\nКонтекст: {ctx[:400]}"},
            ]
            raw = llm_call(prompt)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()[:280]
        except Exception as exc:
            logger.warning("hint_for: LLM-подсказка недоступна (%s) — rule-based", exc)

    if level == 2 and ca:
        words = re.findall(r"\S+", ca)
        if words:
            return f"Начни так: «{' '.join(words[:7])}…»"

    base = ca or ctx or "материал"
    if level == 1:
        kws = _keywords(base)
        if kws:
            return f"Подумай, что связывает: {', '.join(kws)}."
    # последний fallback
    return "Перечитай материал по теме и попробуй ещё раз — ответ рядом."


def subtask_step(queue: List[str], index: int) -> str:
    """Текущий шаг декомпозиции (клампинг индекса)."""
    if not queue:
        return ""
    return queue[max(0, min(index, len(queue) - 1))]
