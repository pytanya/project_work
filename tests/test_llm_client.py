"""Тесты LLM-клиента EduTutor (Слайс 1).

Юнит-тесты каскада/retry/стоимости на моках + интеграционные тесты
реальных вызовов RouterAI (если ключ в .env).
"""

from __future__ import annotations

import pytest

from src.config import BASE_DIR, Settings
from src.guardrails import BudgetExceededError, BudgetGuard
from src.llm_client import LLMClient, LLMResponse, _estimate_cost
from src.metrics import MetricsCollector


@pytest.fixture
def make_settings(monkeypatch):
    def _make(**kwargs):
        for name in Settings.model_fields:
            if name not in kwargs:
                monkeypatch.delenv(name, raising=False)
        return Settings(_env_file=None, **kwargs)

    return _make


class TestEstimateCost:
    def test_routerai_qwen_rub_to_usd(self, make_settings):
        s = make_settings()
        usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
        cost = _estimate_cost(usage, provider="routerai", model="qwen/qwen3.7-flash")
        # 3.10₽ * 0.0111 ≈ 0.03441
        assert cost == pytest.approx(3.1 * s.RUB_TO_USD_RATE, rel=1e-6)

    def test_openrouter_usd(self, make_settings):
        usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
        cost = _estimate_cost(usage, provider="openrouter", model="anything")
        assert cost == pytest.approx(0.14)

    def test_empty_usage(self):
        assert _estimate_cost({}) == 0.0


def _fake_response(content: str = "ответ", **kw) -> LLMResponse:
    return LLMResponse(content=content, usage={"prompt_tokens": 10, "completion_tokens": 5}, **kw)


class TestChatCascade:
    """Каскад провайдеров на моках _make_request."""

    def test_success_on_primary(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k", OPENROUTER_API_KEY="k2")
        client = LLMClient(s, role="tutor")
        monkeypatch.setattr(client, "_make_request", lambda *a, **kw: _fake_response(provider="routerai"))
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "ответ"
        assert resp.provider == "routerai"
        assert client.provider_used == ["routerai"]

    def test_fallback_to_secondary(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k", OPENROUTER_API_KEY="k2")
        client = LLMClient(s, role="tutor")

        def fake(client_obj, name, model, messages, tools, tc, mt, temp):
            if name == "routerai":
                raise RuntimeError("primary down")
            return _fake_response(provider="openrouter")

        monkeypatch.setattr(client, "_make_request", fake)
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.provider == "openrouter"

    def test_all_providers_down_raises(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k", OPENROUTER_API_KEY="k2")
        client = LLMClient(s, role="tutor")

        def fake(*a, **kw):
            raise RuntimeError("down")

        monkeypatch.setattr(client, "_make_request", fake)
        with pytest.raises(RuntimeError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_judge_only_routerai(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k", OPENROUTER_API_KEY="k2")
        client = LLMClient(s, role="judge")
        assert [p["name"] for p in client.providers] == ["routerai"]
        monkeypatch.setattr(client, "_make_request", lambda *a, **kw: _fake_response(provider="routerai"))
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.provider == "routerai"

    def test_no_providers_raises(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="", OPENROUTER_API_KEY="")
        with pytest.raises(RuntimeError):
            LLMClient(s, role="tutor")

    def test_unknown_role_rejected(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k")
        with pytest.raises(ValueError):
            LLMClient(s, role="bogus")


class TestCheapMetrics:
    """Метрика «доля отказов дешёвой роли» (В-2)."""

    def test_cheap_success_recorded(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k")
        metrics = MetricsCollector()
        client = LLMClient(s, role="cheap", metrics=metrics)
        monkeypatch.setattr(client, "_make_request", lambda *a, **kw: _fake_response(provider="routerai"))
        client.chat([{"role": "user", "content": "hi"}])
        assert metrics.num_llm_calls == 1
        assert metrics.cheap_refusal_rate == 0.0

    def test_cheap_refusal_recorded(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k")
        metrics = MetricsCollector()
        client = LLMClient(s, role="cheap", metrics=metrics)

        def fake(*a, **kw):
            raise RuntimeError("cheap down")

        monkeypatch.setattr(client, "_make_request", fake)
        with pytest.raises(RuntimeError):
            client.chat([{"role": "user", "content": "hi"}])
        assert metrics.cheap_refusal_rate == 1.0


class TestBudgetEnforcement:
    """Бюджет (В-7): LLMClient блокирует вызовы после исчерпания лимита."""

    def test_budget_exceeded_raises(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k", MAX_COST_USD=0.0)
        budget = BudgetGuard(s)
        budget.record("tutor", 0.01)  # израсходовано > MAX_COST_USD=0 → лимит превышен
        client = LLMClient(s, role="tutor", budget=budget)
        with pytest.raises(BudgetExceededError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_cost_recorded_on_success(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k", MAX_COST_USD=1.0)
        budget = BudgetGuard(s)
        client = LLMClient(s, role="tutor", budget=budget)
        monkeypatch.setattr(
            client, "_make_request",
            lambda *a, **kw: _fake_response(provider="routerai", cost_usd=0.1),
        )
        client.chat([{"role": "user", "content": "hi"}])
        assert budget.spent("tutor") == pytest.approx(0.1)
        assert budget.calls_total == 1

    def test_call_limit_blocks_next(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k", MAX_COST_USD=100.0, MAX_LLM_CALLS_PER_SESSION=1)
        budget = BudgetGuard(s)
        client = LLMClient(s, role="tutor", budget=budget)
        monkeypatch.setattr(
            client, "_make_request",
            lambda *a, **kw: _fake_response(provider="routerai", cost_usd=0.01),
        )
        client.chat([{"role": "user", "content": "hi"}])  # 1-й вызов — ок
        with pytest.raises(BudgetExceededError):
            client.chat([{"role": "user", "content": "hi again"}])  # лимит достигнут


class TestStreamRetry:
    """Ретрай стрима не должен дублировать уже показанные токены в UI."""

    def test_retry_after_partial_stream_suppresses_duplicates(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k")
        client = LLMClient(s, role="tutor")
        client._clients = {"routerai": object()}

        attempts = {"n": 0}
        seen = []

        def fake_stream(client_obj, name, model, messages, on_chunk, mt, temp):
            attempts["n"] += 1
            if attempts["n"] == 1:
                on_chunk("часть1")
                raise ConnectionError("stream dropped mid-way")
            # 2-я попытка успешна, но её чанки НЕ должны повторяться в UI
            on_chunk("часть2")
            return LLMResponse(content="часть1часть2", usage={}, provider=name, model=model, role="tutor")

        resp = client._request_with_retry(
            {"name": "routerai", "base_url": "", "api_key": "k", "model": "m", "timeout": 1},
            "m", [{"role": "user", "content": "hi"}], None, None, None, None,
            stream_fn=fake_stream, on_chunk=seen.append,
        )
        assert attempts["n"] == 2
        # UI видит только токены 1-й попытки; ретрай не дублирует их
        assert seen == ["часть1"]
        # Финальный ответ при этом полный (из 2-й попытки) — придёт через tutor.lesson
        assert resp.content == "часть1часть2"

    def test_retry_before_any_chunk_streams_normally(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k")
        client = LLMClient(s, role="tutor")
        client._clients = {"routerai": object()}

        attempts = {"n": 0}
        seen = []

        def fake_stream(client_obj, name, model, messages, on_chunk, mt, temp):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("dropped before first token")
            on_chunk("целиком")
            return LLMResponse(content="целиком", usage={}, provider=name, model=model, role="tutor")

        resp = client._request_with_retry(
            {"name": "routerai", "base_url": "", "api_key": "k", "model": "m", "timeout": 1},
            "m", [{"role": "user", "content": "hi"}], None, None, None, None,
            stream_fn=fake_stream, on_chunk=seen.append,
        )
        assert attempts["n"] == 2
        # Чанков не было — ретрай стримит как обычно
        assert seen == ["целиком"]
        assert resp.content == "целиком"


class TestRealRouterAI:
    """Интеграционные тесты: реальные вызовы RouterAI (если ключ в .env)."""

    @pytest.mark.skipif(
        not (BASE_DIR / ".env").exists() or not Settings().ROUTERAI_API_KEY,
        reason="Нет ROUTERAI_API_KEY",
    )
    def test_real_tutor_call(self):
        client = LLMClient(role="tutor")
        resp = client.chat(
            [{"role": "user", "content": "Ответь одним словом: ок"}],
            max_tokens=8,
            temperature=0,
        )
        assert resp.content
        assert resp.provider == "routerai"
        assert resp.cost_usd >= 0.0

    @pytest.mark.skipif(
        not (BASE_DIR / ".env").exists() or not Settings().ROUTERAI_API_KEY,
        reason="Нет ROUTERAI_API_KEY",
    )
    def test_real_cheap_call(self):
        client = LLMClient(role="cheap")
        resp = client.chat(
            [{"role": "user", "content": "Ответь одним словом: ок"}],
            max_tokens=8,
            temperature=0,
        )
        assert resp.content
