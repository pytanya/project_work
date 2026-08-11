"""Тесты метрик EduTutor (Слайс 0)."""

from __future__ import annotations

import pytest

from src.metrics import MetricsCollector


class TestMetricsCollector:
    def test_cost_by_role(self):
        m = MetricsCollector()
        m.add_llm_call(cost_usd=0.01, role="cheap")
        m.add_llm_call(cost_usd=0.02, role="tutor")
        m.add_llm_call(cost_usd=0.03, role="expert")
        m.add_llm_call(cost_usd=0.04, role="judge")
        assert m.cost_by_role == {"cheap": 0.01, "tutor": 0.02, "expert": 0.03, "judge": 0.04}
        assert m.total_cost == pytest.approx(0.10)

    def test_cheap_refusal_rate(self):
        m = MetricsCollector()
        m.add_llm_call(role="cheap", status="OK")
        m.add_llm_call(role="cheap", status="OK")
        m.add_llm_call(role="cheap", status="refused")
        assert m.cheap_refusal_rate == pytest.approx(1 / 3, abs=1e-3)

    def test_cheap_refusal_rate_no_calls(self):
        m = MetricsCollector()
        assert m.cheap_refusal_rate == 0.0

    def test_quiz_metrics(self):
        m = MetricsCollector()
        m.record_quiz(correct=3, total=5)
        assert m.quiz_metrics["accuracy"] == pytest.approx(0.6)
        assert m.quiz_metrics["questions"] == 5

    def test_source_finder_metrics(self):
        m = MetricsCollector()
        m.record_source_finder(find_textbook_success=True, sources=3)
        assert m.source_finder_metrics["find_textbook_success"] is True
        assert m.source_finder_metrics["sources"] == 3

    def test_elapsed_sec_without_start(self):
        assert MetricsCollector().elapsed_sec == 0.0

    def test_to_dict_contains_required_keys(self):
        m = MetricsCollector()
        m.start()
        m.stop()
        d = m.to_dict()
        for key in (
            "success", "stop_reason", "elapsed_sec", "total_cost_usd", "cost_by_role",
            "num_llm_calls_by_role", "cheap_refusal_rate", "num_steps",
            "num_llm_calls", "total_tokens", "quiz_metrics", "source_finder_metrics",
        ):
            assert key in d

    def test_total_tokens(self):
        m = MetricsCollector()
        m.add_llm_call(prompt_tokens=100, completion_tokens=50, role="tutor")
        m.add_llm_call(prompt_tokens=10, completion_tokens=20, role="cheap")
        assert m.total_tokens == 180

    def test_unknown_role_ignored_in_by_role(self):
        m = MetricsCollector()
        m.add_llm_call(cost_usd=0.5, role="unknown")
        assert m.cost_by_role["tutor"] == 0.0
        assert m.total_cost == pytest.approx(0.5)
