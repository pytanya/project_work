"""
EduTutor — адаптивная модель ученика: LinUCB contextual bandit (референс: Digiler AI).

Заменяет эвристику «3 верных → ↑ сложность» на контекстный бандит:
- руки (arms) — 3 уровня сложности (easy/medium/hard);
- контекст (features) — мастерство темы, класс, недавний результат, прогресс квиза;
- награда (reward) — score 0..1 от оценки ответа (evaluate_answer);
- выбор руки — LinUCB (disjoint): p_a = x^T θ_a + α·sqrt(x^T A_a⁻¹ x);
- update — A_a += x x^T, b_a += reward·x.

Состояние бандита — JSON-безопасный dict (списки/float/int), хранится в
TutorState.bandit и переживает персистентность (SQLite) и WebSocket-сессии.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .states import TutorState

DIFFICULTY_ORDER = ["easy", "medium", "hard"]

BANDIT_DIM = 4
BANDIT_ALPHA = 0.6
N_ARMS = 3

# Индексы фич контекста (порядок важен: make_bandit d == len(bandit_features))
_F_MASTERY = 0
_F_GRADE = 1
_F_RECENT = 2
_F_PROGRESS = 3


def make_bandit(
    d: int = BANDIT_DIM,
    alpha: float = BANDIT_ALPHA,
    n_arms: int = N_ARMS,
) -> Dict[str, Any]:
    """Создаёт состояние LinUCB: A_a = I_d, b_a = 0 для каждой руки."""
    return {
        "d": d,
        "alpha": alpha,
        "arms": [
            {
                "A": [[1.0 if i == j else 0.0 for j in range(d)] for i in range(d)],
                "b": [0.0] * d,
                "n": 0,
            }
            for _ in range(n_arms)
        ],
    }


def difficulty_arm(difficulty: Optional[str]) -> int:
    """Индекс руки по уровню сложности (easy=0, medium=1, hard=2)."""
    try:
        return DIFFICULTY_ORDER.index(difficulty) if difficulty in DIFFICULTY_ORDER else 1
    except ValueError:
        return 1


def arm_difficulty(arm: int) -> str:
    """Уровень сложности по индексу руки (клампинг в валидный диапазон)."""
    arm = max(0, min(N_ARMS - 1, int(arm)))
    return DIFFICULTY_ORDER[arm]


def _recent_score(state: TutorState) -> float:
    """Средний score01 последних ≤3 ответов (0..1), по умолчанию 0.5."""
    scores = [
        r.get("score01") for r in (state.records or [])[-3:]
        if isinstance(r.get("score01"), (int, float))
    ]
    if not scores:
        return 0.5
    return sum(scores) / len(scores)


def bandit_features(state: TutorState) -> List[float]:
    """Контекст решения (d=BANDIT_DIM): мастерство темы, класс, недавний результат, прогресс."""
    topic = state.current_question.topic if state.current_question else state.topic
    mastery = state.knowledge_map.get(topic, 0.5) if topic else 0.5

    grade_norm = 0.5
    try:
        g = int(state.grade) if state.grade else 0
        grade_norm = min(1.0, max(0.0, g / 11.0))
    except (TypeError, ValueError):
        pass

    progress = min(1.0, state.answered_count / max(1, state.num_questions))
    return [mastery, grade_norm, _recent_score(state), progress]


def bandit_select(
    bandit: Dict[str, Any],
    features: List[float],
    current_idx: int = 1,
) -> int:
    """Выбирает руку по UCB-оценке; при равенстве (холодный старт) — текущая сложность."""
    import numpy as np  # noqa: WPS433

    x = np.asarray(features, dtype=float)
    alpha = float(bandit.get("alpha", BANDIT_ALPHA))
    best: int = current_idx
    best_p = -1e18
    for idx, arm in enumerate(bandit["arms"]):
        A = np.asarray(arm["A"], dtype=float)
        b = np.asarray(arm["b"], dtype=float)
        A_inv = np.linalg.inv(A)
        theta = A_inv @ b
        p = float(x @ theta + alpha * float(np.sqrt(float(x @ A_inv @ x))))
        if p > best_p + 1e-9:
            best, best_p = idx, p
        elif abs(p - best_p) <= 1e-9 and idx == current_idx:
            best = idx
    return best


def bandit_update(
    bandit: Dict[str, Any],
    features: List[float],
    arm: int,
    reward: float,
) -> Dict[str, Any]:
    """Обновляет параметры сыгранной руки: A += x x^T, b += reward·x."""
    import numpy as np  # noqa: WPS433

    arm = max(0, min(N_ARMS - 1, int(arm)))
    x = np.asarray(features, dtype=float)
    armd = bandit["arms"][arm]
    A = np.asarray(armd["A"], dtype=float)
    b = np.asarray(armd["b"], dtype=float)
    A = A + np.outer(x, x)
    b = b + float(reward) * x
    armd["A"] = A.tolist()
    armd["b"] = b.tolist()
    armd["n"] += 1
    return bandit


def update_counters(state: TutorState, correct: bool) -> None:
    """Счётчики сессии без эвристики сложности (используется с бандитом)."""
    state.answered_count += 1
    if correct:
        state.correct_count += 1
        state.correct_streak += 1
        state.wrong_streak = 0
    else:
        state.wrong_streak += 1
        state.correct_streak = 0
