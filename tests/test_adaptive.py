"""Тесты адаптивной модели ученика (LinUCB bandit, src/adaptive.py)."""

from __future__ import annotations

import json

import pytest

from src.adaptive import (
    arm_difficulty,
    bandit_features,
    bandit_select,
    bandit_update,
    difficulty_arm,
    make_bandit,
    update_counters,
)
from src.states import TutorState


def _state(**kw) -> TutorState:
    defaults = dict(num_questions=10, grade="6")
    defaults.update(kw)
    return TutorState(**defaults)


class TestBanditStructure:
    def test_make_bandit_arms(self):
        b = make_bandit()
        assert len(b["arms"]) == 3
        arm = b["arms"][0]
        assert len(arm["A"]) == b["d"] == 4
        assert arm["b"] == [0.0] * 4
        assert arm["n"] == 0

    def test_json_serializable(self):
        b = make_bandit()
        b = bandit_update(b, [0.5, 0.5, 0.5, 0.1], 1, 0.9)
        json.dumps(b)  # не должно падать: только списки/float/int


class TestBanditSelection:
    def test_cold_start_prefers_current(self):
        b = make_bandit()
        f = [0.5, 0.5, 0.5, 0.1]
        assert bandit_select(b, f, current_idx=1) == 1
        assert bandit_select(b, f, current_idx=2) == 2

    def test_update_then_select_prefers_rewarded_arm(self):
        b = make_bandit()
        f = [0.8, 0.5, 0.7, 0.2]
        # много положительных наград на руке 2 (hard), нулевые — на остальных
        for _ in range(6):
            b = bandit_update(b, f, 2, 1.0)
        b = bandit_update(b, f, 0, 0.0)
        b = bandit_update(b, f, 1, 0.0)
        assert bandit_select(b, f, current_idx=0) == 2

    def test_difficulty_mapping_roundtrip(self):
        assert difficulty_arm("easy") == 0
        assert difficulty_arm("medium") == 1
        assert difficulty_arm("hard") == 2
        assert arm_difficulty(0) == "easy"
        assert arm_difficulty(1) == "medium"
        assert arm_difficulty(2) == "hard"
        assert arm_difficulty(5) == "hard"  # клампинг
        assert arm_difficulty(-1) == "easy"


class TestBanditFeatures:
    def test_features_dim(self):
        st = _state(grade="6")
        f = bandit_features(st)
        assert len(f) == 4
        assert f[0] == 0.5  # нет мастерства темы → 0.5

    def test_mastery_reflected(self):
        st = _state(grade="6")
        st.knowledge_map["Атмосфера"] = 0.9
        st.current_question = None
        st.topic = "Атмосфера"
        assert bandit_features(st)[0] == 0.9

    def test_recent_scores(self):
        st = _state(grade="11")
        st.records = [{"score01": 1.0}, {"score01": 0.0}]
        assert bandit_features(st)[2] == 0.5
        assert bandit_features(st)[1] == pytest.approx(1.0)


class TestCounters:
    def test_counters_correct(self):
        st = _state()
        update_counters(st, True)
        assert st.answered_count == 1
        assert st.correct_count == 1
        assert st.correct_streak == 1

    def test_counters_wrong(self):
        st = _state()
        update_counters(st, False)
        update_counters(st, False)
        assert st.wrong_streak == 2
        assert st.correct_streak == 0
