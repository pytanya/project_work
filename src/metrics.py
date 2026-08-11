"""
EduTutor — сбор метрик (адаптация metrics.py из research_guard_agent, 13.1).

MetricsCollector:
- start()/stop() — общее время;
- add_step / add_llm_call — шаги и LLM-вызовы;
- стоимость ПО РОЛЯМ (cheap/tutor/expert/judge);
- «доля отказов дешёвой роли» (В-2) — число отказов cheap-роли к числу вызовов;
- quiz_metrics / source_finder_metrics (intake_success, find_textbook_success и т.д.).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .config import ROLE_CHEAP, ROLE_EXPERT, ROLE_JUDGE, ROLE_TUTOR

ROLES = (ROLE_CHEAP, ROLE_TUTOR, ROLE_EXPERT, ROLE_JUDGE)


class MetricsCollector:
    """Коллектор метрик сессии EduTutor."""

    def __init__(self) -> None:
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        self._steps: List[Dict[str, Any]] = []
        self._llm_calls: List[Dict[str, Any]] = []
        self.success: bool = False
        self.stop_reason: str = ""
        self.quiz_metrics: Dict[str, Any] = {}
        self.source_finder_metrics: Dict[str, Any] = {}

    def start(self) -> None:
        self._started_at = time.monotonic()

    def stop(self) -> None:
        self._stopped_at = time.monotonic()

    @property
    def elapsed_sec(self) -> float:
        if self._started_at is None:
            return 0.0
        end = self._stopped_at or time.monotonic()
        return round(end - self._started_at, 3)

    def add_step(
        self,
        name: str,
        duration: float,
        status: str = "OK",
        cost: float = 0.0,
        detail: str = "",
    ) -> None:
        self._steps.append(
            {
                "step_num": len(self._steps) + 1,
                "tool": name,
                "duration_sec": round(duration, 3),
                "status": status,
                "cost_usd": round(cost, 6),
                "detail": detail,
            }
        )

    def add_llm_call(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        provider: str = "",
        duration: float = 0.0,
        role: str = "",
        status: str = "OK",
    ) -> None:
        """Зафиксировать LLM-вызов. role: cheap/tutor/expert/judge.

        status="refused" — дешёвая роль отказала (таймаут/пустой результат/плохой
        результат) — для метрики «доля отказов дешёвой роли» (В-2).
        """
        self._llm_calls.append(
            {
                "kind": "llm",
                "role": role,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": round(cost_usd, 6),
                "provider": provider,
                "duration_sec": round(duration, 3),
                "status": status,
            }
        )

    # ------------------------------------------------------------------
    @property
    def total_cost(self) -> float:
        step_cost = sum(s.get("cost_usd", 0.0) for s in self._steps)
        llm_cost = sum(c.get("cost_usd", 0.0) for c in self._llm_calls)
        return round(step_cost + llm_cost, 6)

    @property
    def cost_by_role(self) -> Dict[str, float]:
        """Стоимость по ролям (cheap/tutor/expert/judge), USD."""
        result: Dict[str, float] = {role: 0.0 for role in ROLES}
        for c in self._llm_calls:
            role = c.get("role", "")
            if role in result:
                result[role] += c.get("cost_usd", 0.0)
        return {k: round(v, 6) for k, v in result.items()}

    @property
    def num_llm_calls_by_role(self) -> Dict[str, int]:
        result: Dict[str, int] = {role: 0 for role in ROLES}
        for c in self._llm_calls:
            role = c.get("role", "")
            if role in result:
                result[role] += 1
        return result

    @property
    def cheap_refusal_rate(self) -> float:
        """«Доля отказов дешёвой роли» (В-2): refused / всего cheap-вызовов."""
        calls = [c for c in self._llm_calls if c.get("role") == ROLE_CHEAP]
        if not calls:
            return 0.0
        refused = sum(1 for c in calls if c.get("status") == "refused")
        return round(refused / len(calls), 4)

    @property
    def num_steps(self) -> int:
        return len(self._steps)

    @property
    def num_llm_calls(self) -> int:
        return len(self._llm_calls)

    @property
    def total_tokens(self) -> int:
        return sum(
            c.get("prompt_tokens", 0) + c.get("completion_tokens", 0)
            for c in self._llm_calls
        )

    # ------------------------------------------------------------------
    def record_quiz(self, *, correct: int, total: int, questions: int = 0) -> None:
        """Метрики квиза: правильные/всего, доля, число вопросов."""
        self.quiz_metrics = {
            "correct": correct,
            "total": total,
            "questions": questions or total,
            "accuracy": round(correct / total, 4) if total else 0.0,
        }

    def record_source_finder(self, **kwargs: Any) -> None:
        """Метрики сбора источника: успешность find_textbook и др."""
        self.source_finder_metrics = dict(kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stop_reason": self.stop_reason,
            "elapsed_sec": self.elapsed_sec,
            "total_cost_usd": self.total_cost,
            "cost_by_role": self.cost_by_role,
            "num_llm_calls_by_role": self.num_llm_calls_by_role,
            "cheap_refusal_rate": self.cheap_refusal_rate,
            "num_steps": self.num_steps,
            "num_llm_calls": self.num_llm_calls,
            "total_tokens": self.total_tokens,
            "quiz_metrics": self.quiz_metrics,
            "source_finder_metrics": self.source_finder_metrics,
            "steps": list(self._steps),
            "llm_calls": list(self._llm_calls),
        }
