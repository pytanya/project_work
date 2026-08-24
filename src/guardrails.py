"""
EduTutor — guardrails (адаптация guardrails.py из research_guard_agent, 3.7).

1. Prompt-injection фильтр на входе (эвристики).
2. Контент-фильтр: ненормативная лексика, оскорбления, жаргонизмы.
3. Circuit breaker: 3 ошибки подряд → fail closed, half-open cooldown (30с).
4. Бюджеты по ролям (В-7): CHEAP/TUTOR/JUDGE_ALLOWANCE_USD + MAX_COST_USD.
5. Валидация ответа.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from .config import ROLE_CHEAP, ROLE_EXPERT, ROLE_JUDGE, ROLE_TUTOR


class BudgetExceededError(RuntimeError):
    """Бюджет сессии/роли исчерпан: MAX_COST_USD, ролевой allowance или
    MAX_LLM_CALLS_PER_SESSION. Вызывающий слой решает, как сообщить пользователю.
    """


def guard_user_input(text: str) -> Dict[str, Any]:
    """Комбинированная проверка входа: prompt-injection + контент-фильтр.

    Возвращает {blocked, reasons, message, injection, content}. Вызывается на
    границе ввода (API /message и /intake, CLI) до передачи текста агенту.
    """
    injection = check_prompt_injection(text)
    content = check_inappropriate_content(text)
    reasons: List[str] = []
    if injection["injection"]:
        reasons.append("prompt_injection")
    if content["blocked"]:
        reasons.append("content_filter")
    if reasons:
        message = (
            "Сообщение содержит недопустимое содержимое. Пожалуйста, задайте учебный вопрос по теме."
            if content["blocked"]
            else "Сообщение заблокировано как подозрительное — не пытайтесь изменять инструкции агенту."
        )
    else:
        message = ""
    return {
        "blocked": bool(reasons),
        "reasons": reasons,
        "message": message,
        "injection": injection,
        "content": content,
    }

# --- Эвристики prompt-injection (регистронезависимые) ---
INJECTION_PATTERNS: List[str] = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?prior\s+instructions",
    r"disregard\s+(all\s+)?previous\s+instructions",
    r"system\s+prompt",
    r"system\s+message",
    r"developer\s+message",
    r"initial\s+instructions",
    r"forget\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+an?\s+unfiltered",
    r"you\s+are\s+now\s+an?\s+uncensored",
    r"скажи\s+как\s+системный\s+промпт",
    r"забудь\s+(все\s+)?предыдущие\s+инструкции",
    r"игнорируй\s+(все\s+)?предыдущие\s+инструкции",
    r"проигнорируй\s+(все\s+)?предыдущие\s+инструкции",
    r"игнорируй\s+свой\s+системный",
    r"покажи\s+свой\s+системный\s+промпт",
    r"раскрой\s+свой\s+промпт",
    r"do\s+anything\s+now",
    r"act\s+as\s+an?\s+unrestricted",
    r"jailbreak",
    r"new\s+instructions?\s+override",
    r"не\s+выполняй\s+(свои|системные|предыдущие)\s+инструкции",
    r"не\s+следуй\s+(своим|системным|предыдущим)\s+инструкциям",
    r"удали\s+(все\s+)?предыдущие\s+инструкции",
]


def check_prompt_injection(text: str) -> Dict[str, Any]:
    """Проверка входа на prompt-injection. Возвращает {injection, matched, confidence}."""
    text_lower = (text or "").lower()
    matched = [p for p in INJECTION_PATTERNS if re.search(p, text_lower)]
    if matched:
        return {
            "injection": True,
            "matched": matched,
            "confidence": min(0.5 + 0.15 * len(matched), 0.98),
        }
    return {"injection": False, "matched": [], "confidence": 0.0}


# --- Контент-фильтр ---
CONTENT_PATTERNS: Dict[str, List[str]] = {
    "profanity": [
        r"ху(й|я|е|ё|и|ю)",
        r"пизд",
        r"бля(д|ть|ха|дь)?\b",
        r"еб(ал|ат|ан|у|ёт|ёш|ет|ну)",
        r"ёб",
        r"залуп",
        r"муд(ак|ац)",
        r"говн",
        r"гандон",
        r"пидор",
        r"сук(а|ин)",
        r"шлюх",
        r"долбо",
        r"оху(й|ен|ев|ел)",
        r"сос(ать|у|ёт|ёшь|и|ить)",
        r"проститут",
        r"выбляд",
        r"нахер",
        r"\bfuck(ing|er|ed|s|u)?\b",
        r"\bshit(t?y|head)?\b",
        r"\bbitch(es)?\b",
        r"\basshole(s)?\b",
        r"\bdick(head|s)?\b",
        r"\bcunt(s)?\b",
        r"\bmotherfuck(er|ing)?\b",
        r"\bwhore(s)?\b",
        r"\bslut(s)?\b",
        r"\bbastard(s)?\b",
        r"\bcocksucker(s)?\b",
        r"\bpussy\b",
        r"\bwanker(s)?\b",
        r"\btwat(s)?\b",
        r"\bbullshit\b",
    ],
    "insults": [
        r"идиот",
        r"дебил",
        r"кретин",
        r"придурок",
        r"недоумок",
        r"имбицил",
        r"урод",
        r"тупиц",
        r"олух",
        r"мразь",
        r"твар(ь|ью)",
        r"сволоч",
        r"скотин",
        r"\bidiot(s)?\b",
        r"\bmoron(s)?\b",
        r"\bimbecile(s)?\b",
        r"\bstupid\b",
    ],
    "offensive_slang": [
        r"быдло",
        r"чмо\b",
        r"лох(и|и)?\b",
        r"гопник",
        r"алкаш",
        r"шмара",
        r"шалаву?",
        r"черномазый",
        r"чухна",
        r"чурка",
        r"хач",
        r"\bjerk(s)?\b",
        r"\bscumbag(s)?\b",
        r"\bjackass(es)?\b",
        r"\bschmuck(s)?\b",
        r"\bposer(s)?\b",
    ],
}


def check_inappropriate_content(text: str) -> Dict[str, Any]:
    """Контент-фильтр входа. Возвращает {blocked, categories, matched, confidence}."""
    text_lower = (text or "").lower()
    matched: List[str] = []
    categories: Dict[str, List[str]] = {}
    for category, patterns in CONTENT_PATTERNS.items():
        hits = [p for p in patterns if re.search(p, text_lower)]
        if hits:
            categories[category] = hits
            matched.extend(hits)
    if matched:
        return {
            "blocked": True,
            "categories": categories,
            "matched": matched,
            "confidence": min(0.5 + 0.15 * len(matched), 0.98),
        }
    return {"blocked": False, "categories": {}, "matched": [], "confidence": 0.0}


def validate_answer(result: Dict[str, Any]) -> Dict[str, Any]:
    """Валидация финального ответа (answer — непустая строка; sources — список)."""
    errors: List[str] = []
    answer = result.get("answer", "")
    if not isinstance(answer, str) or not answer.strip():
        errors.append("answer пустой или не строка")
    sources = result.get("sources", [])
    if not isinstance(sources, list) or len(sources) == 0:
        errors.append("sources пустой или не список")
    return {"valid": len(errors) == 0, "errors": errors, "data": result}


class CircuitBreaker:
    """Circuit breaker: 3 ошибки подряд → fail closed, half-open cooldown."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        self._consecutive_failures = 0
        self._state: str = "closed"
        self._last_failure_time: float = 0.0

    def record_success(self) -> None:
        if self._state == "half-open":
            self._state = "closed"
            self._consecutive_failures = 0
        elif self._state == "closed":
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        if self._state == "half-open":
            self._state = "open"
            self._last_failure_time = time.monotonic()
            return
        if self._state == "open":
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = "open"
            self._last_failure_time = time.monotonic()

    def is_open(self) -> bool:
        if self._state == "closed":
            return False
        if self._state == "open":
            if time.monotonic() - self._last_failure_time >= self.cooldown_seconds:
                self._state = "half-open"
                return False
            return True
        return False

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._state = "closed"
        self._last_failure_time = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "open": self._state == "open",
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }


class BudgetGuard:
    """Бюджеты по ролям (В-7): CHEAP/TUTOR/JUDGE_ALLOWANCE_USD + MAX_COST_USD.

    Роль тьютора и эксперта делят TUTOR_ALLOWANCE_USD (спецификация 14: «тьютор + эксперт»).
    """

    ROLE_ALLOWANCE_FIELDS = {
        ROLE_CHEAP: "CHEAP_ALLOWANCE_USD",
        ROLE_TUTOR: "TUTOR_ALLOWANCE_USD",
        ROLE_EXPERT: "TUTOR_ALLOWANCE_USD",
        ROLE_JUDGE: "JUDGE_ALLOWANCE_USD",
    }

    def __init__(self, settings: Any = None) -> None:
        from . import config as cfg

        self.settings = settings or cfg.settings
        self._spent: Dict[str, float] = {r: 0.0 for r in self.ROLE_ALLOWANCE_FIELDS}
        self._calls: Dict[str, int] = {r: 0 for r in self.ROLE_ALLOWANCE_FIELDS}
        self._calls_total: int = 0

    def _allowance(self, role: str) -> float:
        field = self.ROLE_ALLOWANCE_FIELDS.get(role)
        return float(getattr(self.settings, field, 0.0)) if field else 0.0

    def _bucket_spent(self, role: str) -> float:
        """Сумма расходов по всем ролям, делящим один бюджет (tutor+expert → TUTOR_ALLOWANCE)."""
        field = self.ROLE_ALLOWANCE_FIELDS.get(role)
        if not field:
            return self._spent.get(role, 0.0)
        return sum(
            v for r, v in self._spent.items()
            if self.ROLE_ALLOWANCE_FIELDS.get(r) == field
        )

    def record(self, role: str, cost_usd: float) -> None:
        self._spent.setdefault(role, 0.0)
        self._spent[role] += cost_usd
        self._calls.setdefault(role, 0)
        self._calls[role] += 1
        self._calls_total += 1

    def spent(self, role: str) -> float:
        return self._spent.get(role, 0.0)

    def spent_total(self) -> float:
        return round(sum(self._spent.values()), 6)

    @property
    def calls_total(self) -> int:
        return self._calls_total

    def exceeded(self, role: str) -> bool:
        """Превышен ли бюджет роли (общая корзина ролей), общий MAX_COST_USD
        или лимит числа LLM-вызовов сессии MAX_LLM_CALLS_PER_SESSION."""
        if self._bucket_spent(role) > self._allowance(role):
            return True
        if self.spent_total() > self.settings.MAX_COST_USD:
            return True
        if self._calls_total >= self.settings.MAX_LLM_CALLS_PER_SESSION:
            return True
        return False

    def allowed(self, role: str) -> bool:
        """Можно ли ещё делать вызовы роли (не превышен бюджет)."""
        return not self.exceeded(role)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spent_by_role": {k: round(v, 6) for k, v in self._spent.items()},
            "calls_by_role": dict(self._calls),
            "spent_total_usd": self.spent_total(),
            "max_cost_usd": self.settings.MAX_COST_USD,
            "max_calls_per_session": self.settings.MAX_LLM_CALLS_PER_SESSION,
        }
