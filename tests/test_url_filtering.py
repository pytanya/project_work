"""Unit-тесты для функции _is_url_like из knowledge_graph.py."""

from __future__ import annotations

import pytest

# Импортируем функцию напрямую — она должна быть экспортирована из модуля
# Но так как она начинается с подчеркивания, импортируем через _
from src.knowledge_graph import (
    KnowledgeGraph,
    PART_OF,
    _is_url_like,
    build_textbook_graph,
)


class TestIsUrlLike:
    """Тесты для функции обнаружения URL-адресов в строках."""

    def test_detects_http_url(self):
        """Строки с http:// должны определяться как URL."""
        assert _is_url_like("http://example.com") is True
        assert _is_url_like("https://infourouk.ru/resource") is True
        assert _is_url_like("http://test.org/path?q=1") is True

    def test_detects_https_url(self):
        """Строки с https:// должны определяться как URL."""
        assert _is_url_like("https://wikipedia.org") is True
        assert _is_url_like("https://www.google.com/search") is True

    def test_detects_www_urls(self):
        """Строки с www. должны определяться как URL."""
        assert _is_url_like("www.example.com") is True
        assert _is_url_like("www.infourouk.ru") is True

    def test_detects_domain_names(self):
        """Полные доменные имена должны определяться как URL."""
        assert _is_url_like("infourouk.ru") is True
        assert _is_url_like("example.com") is True
        assert _is_url_like("test.org") is True
        assert _is_url_like("site.net") is True
        assert _is_url_like("my-site.io") is True

    def test_rejects_normal_titles(self):
        """Нормальные заголовки тем НЕ должны определяться как URL."""
        assert _is_url_like("Основания солей") is False
        assert _is_url_like("Кислоты и их свойства") is False
        assert _is_url_like("Металлы и Неметаллы") is False
        assert _is_url_like("Урок 1. Россия — наша Родина") is False
        assert _is_url_like("Атмосфера и климат") is False
        assert _is_url_like("Периодическая таблица") is False

    def test_rejects_short_titles(self):
        """Короткие строки не должны быть URL."""
        assert _is_url_like("вода") is False
        assert _is_url_like("земля") is False
        assert _is_url_like("О2") is False

    def test_empty_and_none(self):
        """Пустые и None значения должны возвращать False."""
        assert _is_url_like("") is False
        assert _is_url_like(None) is False  # type: ignore

    def test_mixed_content_with_url_pattern(self):
        """Строки, содержащие URL-паттерны внутри, должны быть отфильтрованы."""
        assert _is_url_like("Ссылка: infourouk.ru/notes") is True
        assert _is_url_like("Подробнее на www.example.com") is True


class TestKnowledgeGraphUrlFiltering:
    """Тесты что KnowledgeGraph.add_topic фильтрует URL."""

    def test_add_topic_rejects_url(self):
        """add_topic должен игнорировать узлы с URL-заголовками."""
        kg = KnowledgeGraph()
        # Пытаемся добавить узел с URL
        kg.add_topic("url:test", "https://infourouk.ru")
        # Узел НЕ должен быть добавлен
        assert "url:test" not in kg.graph.nodes

    def test_add_topic_accepts_normal_title(self):
        """add_topic должен добавлять узлы с нормальными заголовками."""
        kg = KnowledgeGraph()
        kg.add_topic("topic:1", "Основания солей")
        assert "topic:1" in kg.graph.nodes
        data = kg.graph.nodes["topic:1"]
        assert data["title"] == "Основания солей"

    def test_add_topic_accepts_similar_but_not_url(self):
        """Заголовки похожие на URL но не являющиеся ими, должны приниматься."""
        kg = KnowledgeGraph()
        kg.add_topic("topic:ph", "pH и водородные показатели")
        assert "topic:ph" in kg.graph.nodes

        kg.add_topic("topic:no2", "Диоксид азота NO2")
        assert "topic:no2" in kg.graph.nodes


class TestBuildTextbookGraphNoUrls:
    """Тесты что build_textbook_graph фильтрует URL-узлы."""

    def test_web_headings_filter_urls(self):
        """Веб-конспект с URL в заголовках должен фильтровать их."""
        text = """
# Иммануил Кант
Введение.

## infourouk.ru/conспекты
Это шумовой URL.

## Критика чистого разума
Настоящая тема.

## www.wikipedia.org/Кант
Ещё один URL.

## Биография Канта
Ещё настоящая тема.
"""
        kg = build_textbook_graph(text, source="kant")
        titles = [d["title"] for _, d in kg.graph.nodes(data=True)]
        
        # Настоящие темы должны быть
        assert "Критика чистого разума" in titles
        assert "Биография Канта" in titles
        
        # URL должны быть отфильтрованы
        assert "infourouk.ru/conспекти" not in titles
        # Проверяем что URL-строки не попали
        for t in titles:
            assert "infourouk" not in t.lower(), f"URL найден в теме: {t}"
            assert "wikipedia" not in t.lower(), f"URL найден в теме: {t}"

    def test_topic_count_excludes_url_nodes(self):
        """Количество тем не должно включать URL-узлы."""
        text = """
# Тема 1
Текст.

## example.com/shum
Шум.

## Тема 2
Текст.

## https://spam.ru/page
Ещё шум.

## Тема 3
Текст.
"""
        kg = build_textbook_graph(text, source="test")
        nodes_data = list(kg.graph.nodes(data=True))
        
        # Подсчитываем非-book темы
        topics = [n for n, d in nodes_data if d.get("type") != "book"]
        # Должны быть только 3 реальные темы
        assert len(topics) == 3
