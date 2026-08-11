"""Тесты EduTutorEval (Слайс 8, В-6/В-9): mock-прогон сценариев + intent accuracy."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

from edututor_eval import intent_accuracy, run_all, run_scenario  # noqa: E402

from src.config import Settings  # noqa: E402


@pytest.fixture
def make_settings(monkeypatch):
    def _make(**kwargs):
        for name in Settings.model_fields:
            if name not in kwargs:
                monkeypatch.delenv(name, raising=False)
        return Settings(_env_file=None, **kwargs)

    return _make


class TestIntentAccuracy:
    def test_accuracy_at_least_0_8(self):
        assert intent_accuracy() >= 0.8


class TestRunScenario:
    def test_mock_schoolchild(self, make_settings):
        from edututor_eval import GOLDEN_SET, build_mock_deps, load_json

        s = make_settings()
        deps = build_mock_deps(s)
        scenario = next(
            sc for sc in load_json(GOLDEN_SET)["scenarios"]
            if sc["id"] == "schoolchild_grade6_geography"
        )
        result = run_scenario(scenario, deps, questions=2, mock=True)
        assert result["intake_success"] is True
        assert result["find_textbook_success"] is True
        assert result["judge_score_evaluation"] is not None

    def test_mock_no_materials_fails(self, make_settings):
        from edututor_eval import GOLDEN_SET, build_mock_deps, load_json

        s = make_settings()
        deps = build_mock_deps(s)
        scenario = next(
            sc for sc in load_json(GOLDEN_SET)["scenarios"] if sc["id"] == "no_materials"
        )
        result = run_scenario(scenario, deps, questions=1, mock=True)
        assert result["source_failed"] is True
        assert result["find_textbook_success"] is False


class TestRunAll:
    def test_mock_all_scenarios(self, make_settings):
        s = make_settings()
        results = run_all(runs=1, mock=True)
        assert results["intent_accuracy"] >= 0.8
        first = results["runs"][0]["scenarios"]
        by_id = {r["scenario"]: r for r in first}
        assert by_id["schoolchild_grade6_geography"]["intake_success"] is True
        assert by_id["student_with_pdf"]["intake_success"] is True
        assert by_id["no_materials"]["source_failed"] is True
