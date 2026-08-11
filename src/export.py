"""
EduTutor — экспорт для учителя (расширение 15.1 п.7, раздел 13.2).

CSV-файлы на сессию (UTF-8 с BOM — корректно открывается в Excel):
1. <session_id>_questions.csv — вопросы квиза: тема/параграф/вопрос/варианты/
   ответ ученика/оценка/фидбек/модель/судья.
2. <session_id>_summary.csv — сводка сессии: тип/класс/предмет/тема/режим,
   correct/total, accuracy, knowledge_map, стоимость/время.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings as default_settings

QUESTION_COLUMNS = [
    "timestamp", "session_id", "topic", "section", "question_id", "question",
    "options", "answer_type", "difficulty", "student_answer", "score01",
    "correct", "feedback", "model_used", "judge_score",
]

SUMMARY_COLUMNS = [
    "session_id", "timestamp", "learner_type", "grade", "subject", "topic",
    "curriculum", "mode", "difficulty", "num_questions", "correct", "total",
    "accuracy", "knowledge_map", "source_status", "session_status",
    "total_cost_usd", "elapsed_sec",
]


def _csv_text(columns: List[str], rows: List[List[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue()


def questions_csv(records: List[Dict[str, Any]], session_id: str = "") -> str:
    """CSV по записям вопросов сессии (state.records)."""
    rows = []
    for r in records:
        options = r.get("options")
        options_str = " | ".join(options) if isinstance(options, list) else ""
        rows.append([
            r.get("timestamp", ""), session_id, r.get("topic", ""), r.get("section", ""),
            r.get("question_id", ""), r.get("question", ""), options_str,
            r.get("answer_type", ""), r.get("difficulty", ""), r.get("student_answer", ""),
            r.get("score01", ""), r.get("correct", ""), r.get("feedback", ""),
            r.get("model_used", ""), r.get("judge_score", ""),
        ])
    return _csv_text(QUESTION_COLUMNS, rows)


def summary_row(
    state: Any,
    session_id: str = "",
    total_cost_usd: float = 0.0,
    elapsed_sec: float = 0.0,
) -> List[Any]:
    """Одна строка сводки сессии."""
    total = state.answered_count
    correct = state.correct_count
    accuracy = round(correct / total, 4) if total else 0.0
    return [
        session_id, "", getattr(state, "learner_type", ""), getattr(state, "grade", ""),
        getattr(state, "subject", ""), getattr(state, "topic", ""),
        getattr(state, "curriculum", ""), getattr(state, "mode", ""),
        getattr(state, "difficulty", ""), getattr(state, "num_questions", 0),
        correct, total, accuracy,
        {k: round(v, 2) for k, v in getattr(state, "knowledge_map", {}).items()},
        getattr(state, "source_status", ""), getattr(state, "session_status", ""),
        round(total_cost_usd, 6), round(elapsed_sec, 3),
    ]


def summary_csv(state: Any, session_id: str = "", total_cost_usd: float = 0.0, elapsed_sec: float = 0.0) -> str:
    return _csv_text(SUMMARY_COLUMNS, [summary_row(state, session_id, total_cost_usd, elapsed_sec)])


def write_session_exports(
    state: Any,
    session_id: str,
    out_dir: Optional[Path] = None,
    total_cost_usd: float = 0.0,
    elapsed_sec: float = 0.0,
) -> Dict[str, Path]:
    """Запись вопросов + сводки в out_dir. Возвращает {kind: path}."""
    out_dir = out_dir or default_settings.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    q_path = out_dir / f"{session_id}_questions.csv"
    s_path = out_dir / f"{session_id}_summary.csv"

    q_path.write_text(questions_csv(state.records, session_id), encoding="utf-8-sig")
    s_path.write_text(summary_csv(state, session_id, total_cost_usd, elapsed_sec), encoding="utf-8-sig")

    return {"questions": q_path, "summary": s_path}
