"""Тесты реестра инструментов (function calling, раздел 7.2)."""

from __future__ import annotations

import json

import pytest

from src.nlp import Entities
from src.tools import TOOL_FUNCTIONS, execute_tool, rag_search


class TestToolRegistry:
    def test_all_expected_tools_present(self):
        for name in [
            "search_web", "fetch_url", "fetch_html", "crawl_page_js",
            "download_file", "verify_textbook", "process_document",
            "rag_search", "classify_intent", "extract_entities", "save_progress",
        ]:
            assert name in TOOL_FUNCTIONS

    def test_unknown_tool(self):
        res = json.loads(execute_tool("nope"))
        assert res["ok"] is False

    def test_classify_intent_via_execute(self):
        res = execute_tool("classify_intent", '{"query": "Сделай тест"}')
        assert res == "quiz"

    def test_extract_entities_via_execute(self):
        out = execute_tool("extract_entities", '{"query": "6 класс география Алексеев параграф 12"}')
        assert "6" in str(out)
        assert "алексеев" in str(out).lower()

    def test_save_progress(self):
        assert "сохранён" in execute_tool("save_progress")

    def test_rag_search_without_store(self):
        res = json.loads(execute_tool("rag_search", '{"query": "x"}'))
        assert res["ok"] is False

    def test_rag_search_with_store(self):
        class FakeStore:
            def search(self, query, k=5):
                return []

        res = json.loads(execute_tool("rag_search", '{"query": "атмосфера"}', store=FakeStore()))
        assert res["ok"] is True
        assert res["results"] == []

    def test_fetch_url_bad_scheme(self):
        res = json.loads(execute_tool("fetch_url", '{"url": "ftp://x"}'))
        assert res["ok"] is False

    def test_bad_json_arguments(self):
        res = execute_tool("save_progress", "not json")
        assert "сохранён" in res
