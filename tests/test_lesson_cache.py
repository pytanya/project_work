"""Тесты кэша сгенерированных уроков (src/lesson_cache.py)."""

from pathlib import Path

import pytest

from api.schemas import Lesson, LessonSection
from src.lesson_cache import _cache_key, load_lesson, save_lesson


def _lesson() -> Lesson:
    return Lesson(
        title="Атмосфера",
        hook="Почему небо голубое?",
        definition="Атмосфера — газовая оболочка Земли.",
        key_terms=[{"term": "атмосфера", "definition": "воздушная оболочка"}],
        sections=[LessonSection(heading="Состав", body="Воздух содержит азот и кислород.")],
        summary="Атмосфера защищает жизнь.",
    )


class TestLessonCache:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        cache_dir = tmp_path / "cache"
        lesson = _lesson()
        path = save_lesson(cache_dir, "stu_1", "география", "Атмосфера", "6", lesson)
        assert path is not None and path.exists()
        loaded = load_lesson(cache_dir, "stu_1", "география", "Атмосфера", "6")
        assert loaded is not None
        assert loaded.title == lesson.title
        assert loaded.hook == lesson.hook
        assert loaded.sections[0].heading == "Состав"
        assert loaded.definition == lesson.definition

    def test_missing_returns_none(self, tmp_path: Path):
        assert load_lesson(tmp_path / "cache", "stu_1", "география", "Нет темы", "6") is None

    def test_empty_topic_returns_none(self, tmp_path: Path):
        assert load_lesson(tmp_path / "cache", "stu_1", "география", "", "6") is None

    def test_key_isolation_between_students(self, tmp_path: Path):
        cache_dir = tmp_path / "cache"
        lesson = _lesson()
        save_lesson(cache_dir, "stu_1", "география", "Атмосфера", "6", lesson)
        # другой ученик с той же темой — кэш пуст
        assert load_lesson(cache_dir, "stu_2", "география", "Атмосфера", "6") is None
        # другой класс — кэш пуст
        assert load_lesson(cache_dir, "stu_1", "география", "Атмосфера", "7") is None

    def test_key_stable(self):
        assert _cache_key("a", "b", "c", "d") == _cache_key("A", "B", "C", "D")
        assert _cache_key("a", "b", "c", "d") != _cache_key("x", "b", "c", "d")

    def test_corrupt_json_returns_none(self, tmp_path: Path):
        cache_dir = tmp_path / "cache"
        key = _cache_key("stu_1", "география", "Атмосфера", "6")
        lessons = cache_dir / "lessons"
        lessons.mkdir(parents=True)
        (lessons / f"lesson_{key}.json").write_text("{broken", encoding="utf-8")
        assert load_lesson(cache_dir, "stu_1", "география", "Атмосфера", "6") is None
