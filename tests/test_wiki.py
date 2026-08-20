"""Тесты Knowledge Wiki (roadmap #2): персистентные статьи по темам между сессиями."""

from __future__ import annotations

import pytest

from src.wiki import KnowledgeWiki, WikiArticle


class TestWikiArticle:
    def test_frontmatter_okf(self):
        art = WikiArticle(subject="Философия", topic="Кант", grade="студент")
        fm = art.frontmatter()
        assert fm["okf_version"] == "0.2"
        assert fm["type"] == "Topic"
        assert fm["subject"] == "Философия"
        assert fm["title"] == "Кант"
        assert fm["mastery"] == 0.5

    def test_to_markdown_roundtrip(self):
        art = WikiArticle(subject="Философия", topic="Кант", mastery=0.8, attempts=4, correct=3)
        md = art.to_markdown()
        assert md.startswith("---")
        assert "# Кант" in md

    def test_apply_result(self):
        art = WikiArticle(subject="Философия", topic="Кант", mastery=0.5)
        art.apply_result("Кант", 1.0, True)
        assert art.attempts == 1
        assert art.correct == 1
        assert art.mastery > 0.5  # 0.7*0.5 + 0.3*1.0 = 0.65
        art.apply_result("Кант", 0.0, False, "Ошибка: перепутал априорное и апостериорное")
        assert art.attempts == 2
        assert art.correct == 1
        assert len(art.notes) == 1
        assert "априорное" in art.notes[0]

    def test_accuracy(self):
        art = WikiArticle(subject="Философия", topic="Кант", attempts=4, correct=3)
        assert art.accuracy == 0.75
        assert WikiArticle(subject="x", topic="y").accuracy == 0.0


class TestKnowledgeWiki:
    def test_upsert_and_get(self, tmp_path):
        wiki = KnowledgeWiki(tmp_path)
        art = WikiArticle(subject="Философия", topic="Кант", mastery=0.8)
        wiki.upsert(art)

        loaded = wiki.get("Философия", "Кант")
        assert loaded is not None
        assert loaded.topic == "Кант"
        assert loaded.mastery == 0.8

    def test_get_missing_returns_none(self, tmp_path):
        wiki = KnowledgeWiki(tmp_path)
        assert wiki.get("Философия", "Нет") is None

    def test_persistence_across_instances(self, tmp_path):
        """Wiki персистентна: статьи переживают пересоздание хранилища (между сессиями)."""
        wiki1 = KnowledgeWiki(tmp_path)
        wiki1.upsert(WikiArticle(subject="Философия", topic="Кант", mastery=0.7, attempts=2))

        wiki2 = KnowledgeWiki(tmp_path)
        art = wiki2.get("Философия", "Кант")
        assert art is not None
        assert art.mastery == 0.7
        assert art.attempts == 2

    def test_list_subjects_and_articles(self, tmp_path):
        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="Философия", topic="Кант"))
        wiki.upsert(WikiArticle(subject="Философия", topic="Гегель"))
        wiki.upsert(WikiArticle(subject="География", topic="Атмосфера"))

        assert set(wiki.list_subjects()) == {"философия", "география"}
        assert {a.topic for a in wiki.list_articles("Философия")} == {"Кант", "Гегель"}
        assert len(wiki.list_articles()) == 3

    def test_apply_record_and_sync_mastery(self, tmp_path):
        """apply_record (идемпотентно) + sync_mastery (без attempts++) накапливают статьи."""
        from types import SimpleNamespace

        wiki = KnowledgeWiki(tmp_path)
        state = SimpleNamespace(
            subject="Философия",
            grade="студент",
            curriculum="",
            topic="Кант",
            knowledge_map={"Кант": 0.8, "Гегель": 0.3},
            records=[
                {
                    "question_id": "q1",
                    "topic": "Кант",
                    "score01": 0.9,
                    "correct": True,
                    "feedback": "",
                },
                {
                    "question_id": "q2",
                    "topic": "Кант",
                    "score01": 0.2,
                    "correct": False,
                    "feedback": "Ошибка в категорическом императиве",
                },
            ],
        )
        # применяем каждый ответ по отдельности (как в evaluate_answer_node)
        wiki.apply_record(state, state.records[0])
        wiki.apply_record(state, state.records[1])
        # итоговая синхронизация mastery (summary_node)
        wiki.sync_mastery(state)

        kant = wiki.get("Философия", "Кант")
        assert kant is not None
        assert kant.attempts == 2  # идемпотентно: по 1 за каждый ответ
        assert kant.correct == 1
        assert any("категорическом императиве" in n for n in kant.notes)
        # повторная sync_mastery не увеличивает attempts
        wiki.sync_mastery(state)
        assert wiki.get("Философия", "Кант").attempts == 2

        gege = wiki.get("Философия", "Гегель")
        assert gege is not None and gege.mastery == 0.3

    def test_to_summary_dict(self, tmp_path):
        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="Философия", topic="Кант", mastery=0.9))
        summary = wiki.to_summary_dict()
        assert len(summary) == 1
        assert summary[0]["subject"] == "философия"
        assert summary[0]["articles"][0]["topic"] == "Кант"
        assert summary[0]["articles"][0]["mastery"] == 0.9

    def test_enrich_body_with_llm(self, tmp_path):
        """Wiki-LLM: тело статьи генерируется из RAG-контекста."""
        from types import SimpleNamespace

        wiki = KnowledgeWiki(tmp_path)
        state = SimpleNamespace(subject="Философия", grade="студент")
        calls = {"n": 0}

        def fake_llm(messages):
            calls["n"] += 1
            return "Кант — основоположник критической философии. Разработал категорический императив."

        art = wiki.enrich_body(state, "Кант", ["Кант создал «Критику чистого разума».", "Нравственный закон."], llm_call=fake_llm)
        assert art is not None
        assert "категорический императив" in (art.body or "").lower()
        assert calls["n"] == 1
        # статья персистентна
        loaded = wiki.get("Философия", "Кант")
        assert "Кант" in (loaded.body or "")

    def test_enrich_body_no_llm_keeps_shell(self, tmp_path):
        from types import SimpleNamespace

        wiki = KnowledgeWiki(tmp_path)
        state = SimpleNamespace(subject="Философия")
        art = wiki.enrich_body(state, "Кант", ["контекст"], llm_call=None)
        assert art is not None
        assert not (art.body or "").strip()

    def test_enrich_body_llm_empty_result(self, tmp_path):
        """Пустой/короткий результат LLM не затирает статью."""
        from types import SimpleNamespace

        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="Философия", topic="Кант", body="Старое содержимое."))
        state = SimpleNamespace(subject="Философия")

        def fake_llm(messages):
            return ""

        wiki.enrich_body(state, "Кант", ["контекст"], llm_call=fake_llm)
        loaded = wiki.get("Философия", "Кант")
        assert (loaded.body or "").strip() == "Старое содержимое."
