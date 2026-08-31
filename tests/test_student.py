"""Тесты персистентных профилей учеников (src/student.py)."""

from __future__ import annotations

import pytest

from src.student import StudentProfile, StudentStore


@pytest.fixture
def store(tmp_path):
    return StudentStore(tmp_path / "students")


class TestStudentStore:
    def test_get_or_create_persists(self, store):
        p = store.get_or_create("stu_abc")
        assert p.student_id == "stu_abc"
        assert p.name == ""
        # перечитываем с диска — профиль сохранился
        p2 = store.get("stu_abc")
        assert p2 is not None and p2.student_id == "stu_abc"

    def test_save_and_load_fields(self, store):
        p = store.get_or_create("stu_xyz")
        p.name = "Маша"
        p.learner_type = "schoolchild"
        p.grade = "6"
        store.save(p)
        loaded = store.get("stu_xyz")
        assert loaded.name == "Маша"
        assert loaded.learner_type == "schoolchild"
        assert loaded.grade == "6"
        assert loaded.created_at and loaded.updated_at

    def test_get_missing_returns_none(self, store):
        assert store.get("nope") is None

    def test_list_and_delete(self, store):
        store.get_or_create("stu_1")
        store.get_or_create("stu_2")
        assert set(store.list_ids()) == {"stu_1", "stu_2"}
        assert store.delete("stu_1") is True
        assert store.delete("stu_1") is False
        assert store.list_ids() == ["stu_2"]

    def test_prefill(self):
        p = StudentProfile(student_id="stu_1", name="Петя", learner_type="student", grade="10")
        pre = p.prefill()
        assert pre["student_id"] == "stu_1"
        assert pre["student_name"] == "Петя"
        assert pre["learner_type"] == "student"
        assert pre["grade"] == "10"

    def test_session_log_isolated_per_student(self, store):
        store.log_session("stu_a", {"ts": "2026-08-25T10:00:00", "topic": "Атмосфера", "correct": 3, "answered": 4})
        store.log_session("stu_a", {"ts": "2026-08-25T11:00:00", "topic": "Материки", "correct": 5, "answered": 5})
        store.log_session("stu_b", {"ts": "2026-08-25T12:00:00", "topic": "Кант", "correct": 1, "answered": 3})

        a = store.list_sessions("stu_a")
        # последние сверху
        assert [s["topic"] for s in a] == ["Материки", "Атмосфера"]
        assert a[0]["correct"] == 5
        b = store.list_sessions("stu_b")
        assert [s["topic"] for s in b] == ["Кант"]
        assert store.list_sessions("stu_missing") == []

    def test_session_log_respects_limit(self, store):
        for i in range(10):
            store.log_session("stu_l", {"ts": f"2026-08-25T10:{i:02d}:00", "topic": f"t{i}", "answered": 0})
        items = store.list_sessions("stu_l", limit=4)
        assert len(items) == 4
        assert [s["topic"] for s in items] == ["t9", "t8", "t7", "t6"]

    def test_session_log_empty_sid_ignored(self, store):
        store.log_session("", {"topic": "x"})
        store.log_session(None, {"topic": "x"})
        assert store.list_sessions("") == []


from src.student_kg import StudentKnowledgeGraph

def test_set_topic_respects_explicit_status():
    kg = StudentKnowledgeGraph(student_id="s1")
    ts = kg.set_topic(topic_id="t1", status="in_progress")
    assert ts.status == "in_progress"

def test_mark_in_progress_sets_status():
    kg = StudentKnowledgeGraph(student_id="s1")
    ts = kg.mark_in_progress("t1", title="Тема 1")
    assert ts.status == "in_progress"
    assert ts.attempts == 0

def test_mark_in_progress_does_not_downgrade_mastered():
    kg = StudentKnowledgeGraph(student_id="s1")
    kg.set_topic(topic_id="t1", mastery=0.9, attempts=5, correct=5)  # -> mastered
    ts = kg.mark_in_progress("t1")
    assert ts.status == "mastered"

def test_auto_mastery_requires_status_none():
    kg = StudentKnowledgeGraph(student_id="s1")
    ts = kg.set_topic(topic_id="t1", mastery=0.85, attempts=3, correct=3)
    assert ts.status == "mastered"
    ts = kg.set_topic(topic_id="t1", mastery=0.3, attempts=1, correct=0)
    assert ts.status == "in_progress"
