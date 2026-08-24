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
