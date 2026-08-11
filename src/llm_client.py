"""
EduTutor — единый LLM-клиент (доработка llm_client.py из research_guard_agent, 13.1).

Роли (роль задаётся при создании клиента, сигнатура LLMClient(settings, role)):
- tutor  — TUTOR_MODEL (qwen3.7-flash): генерация вопросов, объяснения, оценка в основном потоке;
- expert — EXPERT_MODEL (deepseek-v4-flash): deep-dive объяснения, оценка сложных ответов (Ж-8);
- cheap  — CHEAP_MODEL (google/gemma-3-4b-it): суммаризация, рерайтинг, простые вопросы,
           пре-оценка; отдельный таймаут CHEAP_TIMEOUT_SEC (В-2);
- judge  — JUDGE_MODEL (Gemini на RouterAI, без VPN; OpenRouter для судьи не используется, К-4).

- Primary: RouterAI (LLM_PRIMARY_PROVIDER), Fallback: OpenRouter — fallback по ПРОВАЙДЕРУ
  (те же модели на другом шлюзе), внутри провайдера — по моделям (403/недоступность модели).
- Retry с экспоненциальным backoff при 429/5xx/сетевых ошибках.
- Подсчёт стоимости: поле cost провайдера (RouterAI — в рублях) или приблизительно по токенам.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import openai

from . import config
from .config import ROLE_CHEAP, ROLE_EXPERT, ROLE_JUDGE, ROLE_TUTOR

logger = logging.getLogger("edututor.llm")

PROMPT_PRICE_PER_1M = config.settings.COST_PER_1M_PROMPT
COMPLETION_PRICE_PER_1M = config.settings.COST_PER_1M_COMPLETION

RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # 1 → 2 → 4 сек


@dataclass
class LLMResponse:
    """Результат LLM-вызова."""

    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    cost_raw: float = 0.0
    currency: str = "USD"
    provider: str = ""
    model: str = ""
    role: str = ""


def _estimate_cost(usage: Dict[str, int], provider: str = "", model: str = "") -> float:
    """Приблизительная стоимость по токенам (fallback, если провайдер не вернул cost)."""
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    if provider == "routerai":
        s = config.settings
        if "qwen" in model:
            p, c = s.QWEN_COST_PER_1M_PROMPT_RUB, s.QWEN_COST_PER_1M_COMPLETION_RUB
        else:
            # deepseek и прочие (в т.ч. gemma/gemini) — по базовым рублёвым ценам
            p, c = s.ROUTERAI_COST_PER_1M_PROMPT_RUB, s.ROUTERAI_COST_PER_1M_COMPLETION_RUB
        return (prompt_tokens / 1_000_000 * p + completion_tokens / 1_000_000 * c) * s.RUB_TO_USD_RATE
    return (
        prompt_tokens / 1_000_000 * PROMPT_PRICE_PER_1M
        + completion_tokens / 1_000_000 * COMPLETION_PRICE_PER_1M
    )


class LLMClient:
    """Обёртка над OpenAI-совместимым API с retry и каскадом по провайдерам (раздел 4.1)."""

    def __init__(
        self,
        settings: Optional[config.Settings] = None,
        role: str = ROLE_TUTOR,
        metrics: Any = None,
    ):
        self.settings = settings or config.settings
        self.role = role
        if role not in (ROLE_TUTOR, ROLE_EXPERT, ROLE_CHEAP, ROLE_JUDGE):
            raise ValueError(f"Неизвестная роль: {role!r}")
        self.metrics = metrics

        self.providers = self.settings.provider_configs(role)
        if not self.providers:
            raise RuntimeError(
                "Нет ни одного настроенного LLM-провайдера. "
                "Укажите ROUTERAI_API_KEY (и/или OPENROUTER_API_KEY) в .env"
            )

        self._clients: Dict[str, openai.OpenAI] = {}
        for p in self.providers:
            try:
                client_timeout = p.get("timeout") or self.settings.REQUEST_TIMEOUT
                if role == ROLE_CHEAP:
                    client_timeout = self.settings.CHEAP_TIMEOUT_SEC
                self._clients[p["name"]] = openai.OpenAI(
                    base_url=p["base_url"],
                    api_key=p["api_key"],
                    timeout=client_timeout,
                    max_retries=0,  # retry реализуем сами
                )
            except Exception as e:  # pragma: no cover
                logger.warning("Не удалось создать клиент для %s: %s", p["name"], e)

        if not self._clients:
            raise RuntimeError("Не удалось инициализировать ни одного LLM-клиента")

        self.provider_used: List[str] = []

    # ------------------------------------------------------------------
    def _model_list(self, provider: Dict[str, Any]) -> List[str]:
        """Модели для провайдера: основная роль-модель → fallback-модели (403/недоступность)."""
        provider_model = provider.get("model") or self.settings._role_model(self.role)
        if self.role == ROLE_JUDGE:
            return [provider_model] + [
                m for m in self.settings.judge_fallback_models if m != provider_model
            ]
        return [provider_model] + [
            m for m in self.settings.fallback_models if m != provider_model
        ]

    def _make_request(
        self,
        client: openai.OpenAI,
        provider_name: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Any,
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> LLMResponse:
        """Один запрос к конкретному провайдеру/модели (без retry)."""
        kwargs: Dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature

        resp = client.chat.completions.create(**kwargs)

        message = resp.choices[0].message
        content = message.content
        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]

        usage_raw = resp.usage
        usage: Dict[str, int] = {}
        if usage_raw is not None:
            usage = {
                "prompt_tokens": getattr(usage_raw, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage_raw, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage_raw, "total_tokens", 0) or 0,
            }

        cost = 0.0
        cost_raw = 0.0
        currency = "USD"
        if provider_name == "routerai":
            cost_raw = getattr(resp, "cost", 0.0) or 0.0
            try:
                cost_raw = float(cost_raw)
            except (TypeError, ValueError):
                cost_raw = 0.0
            if cost_raw > 0:
                currency = "RUB"
                cost = cost_raw * config.settings.RUB_TO_USD_RATE
        elif usage_raw is not None and hasattr(usage_raw, "total_cost"):
            raw_cost = getattr(usage_raw, "total_cost", None)
            if raw_cost is not None:
                try:
                    cost = float(raw_cost)
                except (TypeError, ValueError):
                    cost = 0.0
                else:
                    cost_raw = cost

        if cost <= 0.0:
            cost = _estimate_cost(usage, provider=provider_name, model=model)
            if cost > 0:
                cost_raw = cost
                currency = "USD"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            cost_usd=round(cost, 6),
            cost_raw=round(cost_raw, 6),
            currency=currency,
        )

    def _request_with_retry(
        self,
        provider: Dict[str, Any],
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Any,
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> LLMResponse:
        """Запрос с retry на одном провайдере/модели."""
        name = provider["name"]
        client = self._clients.get(name)
        if client is None:
            raise ConnectionError(f"Клиент {name} не инициализирован")

        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._make_request(
                    client, name, model, messages, tools, tool_choice, max_tokens, temperature
                )
                resp.provider = name
                resp.model = model
                resp.role = self.role
                self.provider_used.append(name)
                return resp
            except openai.RateLimitError as e:
                last_error = e
            except openai.APIConnectionError as e:
                last_error = e
            except openai.APITimeoutError as e:
                last_error = e
            except openai.APIStatusError as e:
                last_error = e
                if e.status_code not in RETRY_STATUS_CODES:
                    raise
            except ConnectionError as e:
                last_error = e
            except TimeoutError as e:
                last_error = e
            except Exception as e:
                logger.error("LLM %s: необработанная ошибка: %s", name, e)
                raise

            if attempt < MAX_RETRIES:
                delay = BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "LLM %s [%s]: попытка %d/%d не удалась (%s). Backoff %.1fс",
                    name, model, attempt, MAX_RETRIES, last_error, delay,
                )
                time.sleep(delay)

        raise last_error if last_error else RuntimeError(f"Провайдер {name} исчерпал retry")

    # ------------------------------------------------------------------
    def _record(self, *, status: str = "OK", **kwargs: Any) -> None:
        if self.metrics is not None:
            self.metrics.add_llm_call(role=self.role, status=status, **kwargs)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Пробует провайдеров по приоритету (primary → fallback), внутри — по моделям.

        Для роли cheap при полном отказе фиксирует статус "refused" в метриках
        (В-2: доля отказов дешёвой роли), затем пробрасывает исключение — вызывающий
        слой принимает решение о fallback на TUTOR_MODEL.
        """
        errors: List[str] = []
        providers = list(self.providers)
        for idx, provider in enumerate(providers):
            models = self._model_list(provider)
            for model in models:
                try:
                    resp = self._request_with_retry(
                        provider, model, messages, tools, tool_choice, max_tokens, temperature
                    )
                    self._record(
                        status="OK",
                        prompt_tokens=resp.usage.get("prompt_tokens", 0),
                        completion_tokens=resp.usage.get("completion_tokens", 0),
                        cost_usd=resp.cost_usd,
                        provider=resp.provider,
                    )
                    return resp
                except openai.APIStatusError as e:
                    errors.append(f"{provider['name']}/{model}: {e}")
                    logger.warning("Провайдер %s, модель %s недоступна (%s).", provider["name"], model, e)
                except Exception as e:
                    errors.append(f"{provider['name']}/{model}: {e}")
                    logger.error("Провайдер %s, модель %s не смог обработать запрос. %s", provider["name"], model, e)
                    break  # серьёзная ошибка с моделью провайдера — следующий провайдер

            if idx < len(providers) - 1:
                logger.info("Переключаюсь на fallback-провайдера...")

        if self.role == ROLE_CHEAP:
            self._record(status="refused")
        raise RuntimeError("Все провайдеры и модели недоступны: " + "; ".join(errors))

    # Удобные обёртки
    def tutor(self, messages, **kw) -> LLMResponse:
        return self.chat(messages, **kw)

    def expert(self, messages, **kw) -> LLMResponse:
        return self.chat(messages, **kw)

    def cheap(self, messages, **kw) -> LLMResponse:
        return self.chat(messages, **kw)

    def judge(self, messages, **kw) -> LLMResponse:
        return self.chat(messages, **kw)
