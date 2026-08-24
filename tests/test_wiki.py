"""Тесты Knowledge Wiki (roadmap #2): персистентные статьи по темам между сессиями."""

from __future__ import annotations

import pytest

from src.wiki import KnowledgeWiki, WikiArticle, WikiNote


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
        # notes — List[Dict], проверяем структуру
        note_dict = art.notes[0]
        assert "feedback" in note_dict
        assert "априорное" in note_dict["feedback"]
        assert "date" in note_dict

    def test_apply_result_with_context(self):
        """Проверяем что question/student_answer/correct_answer сохраняются."""
        art = WikiArticle(subject="География", topic="Атмосфера", mastery=0.5)
        art.apply_result(
            "Атмосфера", 0.0, False,
            feedback="Ответ слишком короткий",
            question="Что такое атмосфера?",
            student_answer="газ",
            correct_answer="Атмосфера — газовая оболочка Земли",
        )
        assert len(art.notes) == 1
        note_dict = art.notes[0]
        assert note_dict["question"] == "Что такое атмосфера?"
        assert note_dict["student_answer"] == "газ"
        assert note_dict["correct_answer"] == "Атмосфера — газовая оболочка Земли"
        assert note_dict["feedback"] == "Ответ слишком короткий"
        assert "date" in note_dict

    def test_notes_max_limit(self):
        """Проверяем что сохраняется не больше MAX_NOTES заметок."""
        art = WikiArticle(subject="X", topic="Y", mastery=0.5)
        for i in range(15):
            art.apply_result("Y", 0.0, False, feedback=f"Ошибка #{i}")
        assert len(art.notes) <= WikiArticle.MAX_NOTES  # 10

    def test_notes_deduplication(self):
        """Дедупликация: одинаковый feedback не добавляется дважды."""
        from src.wiki import WikiNote
        art = WikiArticle(subject="X", topic="Y", mastery=0.5)
        art.apply_result("Y", 0.0, False, feedback="Ошибка: коротко")
        art.apply_result("Y", 0.0, False, feedback="Ошибка: коротко")
        assert len(art.notes) == 1
        # Та же ошибка — обновляется дата, но не создаётся дубликат
        note_dict = art.notes[0]
        assert "коротко" in note_dict["feedback"]

    def test_notes_from_dict_format(self):
        """notes как List[Dict] корректно загружается в конструкторе."""
        raw_notes = [
            {"date": "2026-08-19", "feedback": "Ошибка 1"},
            {"date": "2026-08-20", "feedback": "Ошибка 2", "question": "Вопрос?", "student_answer": "нет", "correct_answer": "да"},
        ]
        art = WikiArticle(subject="X", topic="Y", notes=raw_notes)
        assert len(art.notes) == 2
        assert art.notes[0]["feedback"] == "Ошибка 1"
        assert art.notes[1]["question"] == "Вопрос?"

    def test_notes_from_legacy_strings(self):
        """Backwards compatibility: notes как List[str] парсятся автоматически."""
        legacy_notes = ["2026-08-19: Ответ слишком короткий", "2026-08-20: Перепутал понятия"]
        art = WikiArticle(subject="X", topic="Y", notes=legacy_notes)
        assert len(art.notes) == 2
        assert " too short" not in str(art.notes[0])  # не должно быть старого формата
        assert art.notes[0]["date"] == "2026-08-19"
        assert art.notes[0]["feedback"] == "Ответ слишком короткий"

    def test_wiki_note_from_legacy_string(self):
        """WikiNote.from_legacy_string парсит старую строку."""
        note = WikiNote.from_legacy_string("2026-08-19: Ответ слишком короткий")
        assert note.date == "2026-08-19"
        assert note.feedback == "Ответ слишком короткий"
        assert note.question is None

    def test_wiki_note_to_dict(self):
        note = WikiNote(date="2026-08-19", feedback="Коротко", question="Вопрос?")
        d = note.to_dict()
        assert d["date"] == "2026-08-19"
        assert d["feedback"] == "Коротко"
        assert d["question"] == "Вопрос?"
        assert "student_answer" not in d  # None не сериализуется

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
        assert any("категорическом императиве" in n.get("feedback", "") for n in kant.notes)
        # заметки — List[Dict] с полем feedback
        assert all(isinstance(n, dict) and "feedback" in n for n in kant.notes)
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
        assert summary[0]["subject"] == "Философия"  # human-имя, а не slug каталога
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

    def test_from_dict_keeps_human_subject(self, tmp_path):
        """from_dict берёт subject из frontmatter, а не slug каталога."""
        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="Литература", topic="Поэты", mastery=0.7))
        art = wiki.get("Литература", "Поэты")
        assert art is not None and art.subject == "Литература"

    def test_sync_concepts_creates_article(self, tmp_path):
        """Roadmap #3: ключевые понятия темы пишутся в статью (drill-down)."""
        from types import SimpleNamespace

        wiki = KnowledgeWiki(tmp_path)
        state = SimpleNamespace(subject="Философия")
        art = wiki.sync_concepts(state, "Кант", ["императив", "априори"])
        assert art is not None and art.concepts == ["императив", "априори"]
        loaded = wiki.get("Философия", "Кант")
        assert loaded is not None and loaded.concepts == ["императив", "априори"]
        assert "concepts" in loaded.to_dict()

    def test_sync_concepts_updates_existing(self, tmp_path):
        """Понятия перезаписываются последним уроком, статья сохраняет mastery."""
        from types import SimpleNamespace

        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="Философия", topic="Кант", mastery=0.8,
                                concepts=["старое"]))
        state = SimpleNamespace(subject="Философия")
        wiki.sync_concepts(state, "Кант", ["императив"])
        art = wiki.get("Философия", "Кант")
        assert art.concepts == ["императив"]
        assert art.mastery == 0.8

    def test_sync_concepts_skips_empty(self, tmp_path):
        """Пустой список понятий — ничего не пишем."""
        from types import SimpleNamespace

        wiki = KnowledgeWiki(tmp_path)
        state = SimpleNamespace(subject="Философия")
        assert wiki.sync_concepts(state, "Кант", ["", " "]) is None


class TestGraphArticleMatching:
    """Матчинг узлов графа («Урок 3: Тема») с wiki-статьями («Тема») — mastery overlay."""

    def test_normalized_match_lesson_prefix(self, tmp_path):
        from api.routes.graph import _match_article
        from src.wiki import KnowledgeWiki, WikiArticle

        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="Литература", topic="Атмосфера", mastery=0.8))
        art = _match_article(wiki, "Литература", "Урок 3: Атмосфера")
        assert art is not None
        assert art.mastery == 0.8

    def test_exact_match_preferred(self, tmp_path):
        from api.routes.graph import _match_article
        from src.wiki import KnowledgeWiki, WikiArticle

        wiki = KnowledgeWiki(tmp_path)
        wiki.upsert(WikiArticle(subject="Литература", topic="Урок 3: Атмосфера", mastery=0.9))
        wiki.upsert(WikiArticle(subject="Литература", topic="Атмосфера", mastery=0.8))
        art = _match_article(wiki, "Литература", "Урок 3: Атмосфера")
        assert art is not None and art.mastery == 0.9

    def test_no_match_returns_none(self, tmp_path):
        from api.routes.graph import _match_article
        from src.wiki import KnowledgeWiki

        wiki = KnowledgeWiki(tmp_path)
        assert _match_article(wiki, "Философия", "Урок 12: Кант") is None


class TestEvaluationWithWiki:
    """Roadmap #2 (Wiki-LLM): оценка ответа сверяется с wiki-статьёй темы, а не только с RAG-чанками."""

    _EVAL = '{"score": 8, "correct": true, "feedback": "Верно!", "citation_ok": true}'

    def _run(self, tmp_path, store=None):
        from types import SimpleNamespace

        from api.schemas import QuizCard
        from src.evaluation import evaluate_and_record
        from src.states import TutorState

        captured = {}

        def fake_eval(messages):
            captured["ctx"] = messages[-1]["content"]
            return self._EVAL

        deps = SimpleNamespace(
            settings=SimpleNamespace(KNOWLEDGE_WIKI_DIR=str(tmp_path / "wiki")),
            store=store,
            eval_llm=fake_eval,
            judge_llm=lambda m: '{"criteria": {"grade_correct": 9, "feedback_ok": 8, "difficulty_fit": 7}}',
            on_token=None,
        )
        st = TutorState(learner_type="student", subject="Философия", mode="quiz")
        card = QuizCard(
            question_id="q1", question="Кто основоположник критической философии?",
            options=None, answer_type="open", difficulty="medium", topic="Кант",
        )
        evaluate_and_record(st, deps, card, "Кант — основоположник критической философии",
                            emit=lambda *a, **k: None)
        return captured

    def test_wiki_body_added_to_grading_context(self, tmp_path):
        """Статья темы попадает в контекст оценки (когда RAG пуст — она единственный эталон)."""
        wiki = KnowledgeWiki(tmp_path / "wiki")
        wiki.upsert(WikiArticle(subject="Философия", topic="Кант",
                                body="Кант — основоположник критической философии."))
        captured = self._run(tmp_path)
        assert "основоположник критической философии" in captured["ctx"]

    def test_wiki_does_not_override_rag_chunks(self, tmp_path):
        """Wiki-статья дополняет RAG-чанки, а не заменяет их."""
        import hashlib

        from src.knowledge import DocChunk, NumpyVectorStore

        class _Emb:
            def _vec(self, text):
                v = [0.0] * 8
                for token in text.lower().split():
                    h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:4], 16)
                    v[h % 8] += 1.0
                norm = sum(x * x for x in v) ** 0.5 or 1.0
                return [x / norm for x in v]

            def encode(self, texts):
                return [self._vec(t) for t in texts]

            def encode_query(self, text):
                return self._vec(text)

        wiki = KnowledgeWiki(tmp_path / "wiki")
        wiki.upsert(WikiArticle(subject="Философия", topic="Кант", body="Wiki-конспект по Канту."))
        store = NumpyVectorStore("t", _Emb())
        store.add([DocChunk(id="c1", text="Текст из учебника по Канту.", source="book",
                            subject="Философия", grade=None)])
        captured = self._run(tmp_path, store=store)
        assert "Wiki-конспект по Канту" in captured["ctx"]
        assert "Текст из учебника по Канту" in captured["ctx"]

    def test_no_wiki_article_context_unchanged(self, tmp_path):
        captured = self._run(tmp_path)
        assert "Нет контекста по теме" in captured["ctx"]
