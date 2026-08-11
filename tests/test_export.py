"""Тесты экспорта для учителя (CSV, расширение 15.1 п.7)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from src.export import (
    QUESTION_COLUMNS,
    questions_csv,
    summary_csv,
    summary_row,
    write_session_exports,
)
from src.states import TutorState


def _record(**kw):
    base = {
        "timestamp": "2026-08-11T12:00:00",
        "question_id": "q1",
        "question": "Что такое атмосфера?",
        "options": ["А", "Б"],
        "answer_type": "single",
        "difficulty": "easy",
        "topic": "Атмосфера",
        "section": "12",
        "student_answer": "А",
        "score01": 0.8,
        "correct": True,
        "feedback": "Верно!",
        "model_used": "tutor",
        "judge_score": 8.0,
    }
    base.update(kw)
    return base


def _parse(text: str) -> list:
    return list(csv.reader(io.StringIO(text.replace("\ufeff", ""))))


class TestQuestionsCsv:
    def test_header(self):
        text = questions_csv([], "sess-1")
        rows = _parse(text)
        assert rows[0] == QUESTION_COLUMNS

    def test_row_content(self):
        text = questions_csv([_record()], "sess-1")
        rows = _parse(text)
        row = rows[1]
        assert row[1] == "sess-1"          # session_id
        assert row[3] == "12"              # section
        assert "Что такое атмосфера?" in row[5]
        assert "А | Б" == row[6]           # options
        assert row[9] == "А"               # student_answer
        assert row[10] == "0.8"            # score01
        assert row[11] == "True"           # correct
        assert row[13] == "tutor"          # model_used
        assert row[14] == "8.0"            # judge_score

    def test_missing_values_blank(self):
        text = questions_csv([_record(student_answer=None, score01=None)], "s")
        rows = _parse(text)
        assert rows[1][9] == ""
        assert rows[1][10] == ""


class TestSummaryCsv:
    def test_summary_row(self):
        state = TutorState(
            learner_type="schoolchild", grade="6", subject="география", topic="Атмосфера",
            mode="quiz", correct_count=3, answered_count=5,
            knowledge_map={"Атмосфера": 0.65},
        )
        row = summary_row(state, "sess-1")
        assert row[0] == "sess-1"
        assert row[3] == "6"
        assert row[10] == 3 and row[11] == 5  # correct/total
        assert row[12] == 0.6                 # accuracy 3/5

    def test_summary_accuracy_zero(self):
        state = TutorState()
        row = summary_row(state, "s")
        assert row[12] == 0.0


class TestWriteExports:
    def test_writes_two_files(self, tmp_path: Path):
        state = TutorState(records=[_record()], correct_count=1, answered_count=1)
        files = write_session_exports(state, "sess-x", out_dir=tmp_path, total_cost_usd=0.1, elapsed_sec=5.0)
        assert files["questions"].exists()
        assert files["summary"].exists()
        # UTF-8 BOM (Excel)
        raw = files["questions"].read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        rows = _parse(text)
        assert rows[1][0] == "2026-08-11T12:00:00"
