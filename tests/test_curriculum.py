"""Тесты сверки с ФГОС (Слайс 3, В-8)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from src.config import BASE_DIR
from src.curriculum import (
    collect_fgos_via_crawl4ai,
    grade_curriculum,
    load_fgos_reference,
    lookup_fgos,
)

FGOS_DIR = BASE_DIR / "data" / "fgos_reference"


class TestLoadReference:
    def test_loads_offline_base(self):
        ref = load_fgos_reference(FGOS_DIR)
        assert "geography" in ref
        assert "5-6" in ref["geography"]

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        assert load_fgos_reference(tmp_path / "nope") == {}


class TestLookupFgos:
    def test_atmosphere(self):
        ref = load_fgos_reference(FGOS_DIR)
        assert lookup_fgos(ref, "география", "6", "Атмосфера") == "ГЕОГ.5-6.3.1"

    def test_case_insensitive_subject(self):
        ref = load_fgos_reference(FGOS_DIR)
        assert lookup_fgos(ref, "География", "6", "Литосфера") == "ГЕОГ.5-6.2"

    def test_synonym_subject(self):
        ref = load_fgos_reference(FGOS_DIR)
        assert lookup_fgos(ref, "Геогр", "6", "гидросфера") == "ГЕОГ.5-6.4"

    def test_grade_range_match(self):
        ref = load_fgos_reference(FGOS_DIR)
        assert lookup_fgos(ref, "география", "5", "погода") == "ГЕОГ.5-6.3.1"

    def test_unknown_topic_none(self):
        ref = load_fgos_reference(FGOS_DIR)
        assert lookup_fgos(ref, "география", "6", "квантовая физика") is None

    def test_unknown_subject_none(self):
        ref = load_fgos_reference(FGOS_DIR)
        assert lookup_fgos(ref, "астрология", "6", "атмосфера") is None


class TestGradeCurriculum:
    def test_matched(self):
        result = grade_curriculum("география", "6", "Атмосфера", ref_dir=FGOS_DIR)
        assert result.status == "matched"
        assert result.fgos_code == "ГЕОГ.5-6.3.1"

    def test_not_found_honest_warning(self):
        result = grade_curriculum("география", "6", "квантовая физика", ref_dir=FGOS_DIR)
        assert result.status == "not_found"
        assert "не проверена по ФГОС" in result.warning

    def test_reference_unavailable(self, tmp_path: Path):
        result = grade_curriculum("география", "6", "Атмосфера", ref_dir=tmp_path)
        assert result.status == "reference_unavailable"

    def test_llm_match_fallback(self):
        def fake_llm(subject, grade, topic):
            return "ГЕОГ.5-6.3.1-LLM"

        result = grade_curriculum("география", "6", "погодные явления", ref_dir=FGOS_DIR, llm_match=fake_llm)
        assert result.status == "matched"
        assert result.fgos_code == "ГЕОГ.5-6.3.1-LLM"

    def test_llm_not_called_when_base_hits(self):
        called = {"n": 0}

        def fake_llm(subject, grade, topic):
            called["n"] += 1
            return "WRONG"

        grade_curriculum("география", "6", "Атмосфера", ref_dir=FGOS_DIR, llm_match=fake_llm)
        assert called["n"] == 0


@pytest.mark.asyncio
class TestCollectFgosViaCraw4ai:
    _FAKE_MARKDOWN = (
        "## Атмосфера\nсодержание\n## Литосфера\nсодержание\n"
        "### Погода и климат\n"
    )

    async def test_collects_headings_and_saves(self, tmp_path: Path, monkeypatch):
        class FakeResult:
            markdown = TestCollectFgosViaCraw4ai._FAKE_MARKDOWN

        class FakeCrawler:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def arun(self, **kw):
                return FakeResult()

        fake = types.ModuleType("crawl4ai")

        class FakeBrowserConfig:
            def __init__(self, **kw):
                pass

        class FakeRunConfig:
            def __init__(self, **kw):
                pass

        fake.AsyncWebCrawler = FakeCrawler
        fake.BrowserConfig = FakeBrowserConfig
        fake.CrawlerRunConfig = FakeRunConfig
        monkeypatch.setitem(sys.modules, "crawl4ai", fake)

        out_file, topics = await collect_fgos_via_crawl4ai(
            "география", "6", out_dir=tmp_path
        )
        assert out_file.exists()
        assert "Атмосфера" in topics
        data = load_fgos_reference(tmp_path)
        assert "geography" in data
        assert data["geography"]["6"]

    async def test_no_crawl4ai_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.delitem(sys.modules, "crawl4ai", raising=False)
        import builtins

        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "crawl4ai":
                raise ImportError("no crawl4ai")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError):
            await collect_fgos_via_crawl4ai("география", "6", out_dir=tmp_path)
