"""Тесты графа агента (Слайс 7): intake → источник → квиз → оценка → сводка."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from src.config import BASE_DIR, Settings
from src.graph import GraphDeps, build_graph
from src.knowledge import DocChunk, NumpyVectorStore
from src.states import TutorState

FGOS_DIR = BASE_DIR / "data" / "fgos_reference"

_GEN = '{"question": "Что такое атмосфера?", "options": null, "answer_type": "open", "topic": "Атмосфера"}'
_EVAL_OK = '{"score": 8, "correct": true, "feedback": "Верно!", "citation_ok": true}'
_EVAL_WRONG = '{"score": 2, "correct": false, "feedback": "Неверно.", "citation_ok": false}'
_EXPL = '{"text": "Атмосфера — газовая оболочка.", "citation": {"paragraph": "§12", "source": "учебник"}}'
_JUDGE = '{"criteria": {"grade_correct": 9, "feedback_ok": 8, "difficulty_fit": 7}}'


class FakeEmbedder:
    def __init__(self, model="test"):
        self.model = model

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


@pytest.fixture
def make_settings(monkeypatch):
    def _make(**kwargs):
        for name in Settings.model_fields:
            if name not in kwargs:
                monkeypatch.delenv(name, raising=False)
        return Settings(_env_file=None, **kwargs)

    return _make


@pytest.fixture
def deps(make_settings, tmp_path):
    s = make_settings(
        FGOS_REFERENCE_DIR=str(FGOS_DIR),
        TEXTBOOKS_DOWNLOADS_DIR=str(tmp_path / "downloads"),
        MAX_INTAKE_ITERATIONS=8,
        OCR_MIN_TEXT_CHARS=20,
        KNOWLEDGE_GRAPH_DIR=str(tmp_path / "kg"),
        KNOWLEDGE_WIKI_DIR=str(tmp_path / "wiki"),
    )
    embedder = FakeEmbedder()
    store = NumpyVectorStore("t", embedder)
    store.add([
        DocChunk(
            id="c1",
            text="Параграф 12: Атмосфера. Атмосфера — воздушная оболочка Земли, состоит из азота (78%) и кислорода (21%).",
            section_number="12", section_title="Атмосфера", source="book", subject="география", grade="6",
        )
    ])
    return GraphDeps(
        embedder=embedder,
        store=store,
        settings=s,
        tutor_llm=lambda m: _GEN,
        eval_llm=lambda m: _EVAL_OK,
        expert_llm=lambda m: _EXPL,
        judge_llm=lambda m: _JUDGE,
    )


def _invoke(graph, state_dict, config=None):
    """invoke + валидация результата в TutorState."""
    return TutorState.model_validate(graph.invoke(state_dict, config=config))


def _feed(graph, state, answers):
    """Последовательно подаёт ответы пользователя, возвращает финальное состояние."""
    res = _invoke(graph, state.model_dump())
    for ans in answers:
        res = _invoke(graph, {**res.model_dump(), "pending_answer": ans})
    return res


class TestIntakeFlow:
    def test_asks_learner_type_first(self, deps):
        graph = build_graph(deps)
        res = _invoke(graph, TutorState().model_dump())
        assert res.intake_field == "learner_type"
        assert res.agent_question

    def test_full_intake_to_quiz(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=3, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["ученик 6 класса", "география", "Атмосфера", "нет", "квиз"])
        # intake завершён, источник готов (pre-seeded), задан вопрос квиза
        assert res.agent_question
        assert res.current_question is not None
        assert res.source_status == "ready"
        # curriculum вычисляется только при наличии темы; иначе None
        assert res.curriculum in (None, "ГЕОГ.5-6.3.1", "unverified")

    def test_emergency_start_on_ne_znayu(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["не знаю", "не знаю"])
        assert res.agent_question or res.current_question
        assert res.intake_field is None

    def test_full_intake_in_one_message(self, deps):
        """Уровень 2 (5.4): один развёрнутый ответ заполняет весь чек-лист разом."""
        graph = build_graph(deps)
        state = TutorState(num_questions=3, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _invoke(graph, {
            **state.model_dump(),
            "pending_answer": "я в 7 классе, география, тема Атмосфера, учебника нет, хочу квиз",
        })
        assert res.learner_type == "schoolchild"
        assert res.grade == "7"
        assert res.subject == "география"
        assert res.topic == "Атмосфера"
        assert res.has_textbook is False
        assert res.mode == "quiz"
        assert res.missing_fields == []
        assert res.current_question is not None  # intake завершён, квиз сразу


class TestQuizFlow:
    def test_full_quiz_with_knowledge_map_and_summary(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=3, sources=[{"type": "web", "url": "x"}], collection_id="web")
        # intake
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "квиз"])
        # q1 (оценка ОК)
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Атмосфера — это воздушная оболочка."})
        assert res.knowledge_map.get("Атмосфера") is not None
        assert res.agent_message
        # q2
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Воздушная оболочка Земли."})
        assert res.correct_count >= 1
        # q3
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Океан."})
        assert res.quiz_complete is True
        assert res.session_status == "completed"
        assert res.summary_text and "Квиз завершён" in res.summary_text

    def test_closed_wrong_answer_skips_judge_and_expert(self, deps):
        """Закрытый вопрос, неверный выбор → детерминированно «Ошибка» без судьи/эксперта (быстро)."""
        def gen(m):
            return '{"question":"Вопрос?","options":["А","Б"],"answer_type":"single","topic":"Тема","correct_answers":["Б"]}'
        judged = {"n": 0}
        expert = {"n": 0}
        deps.tutor_llm = gen
        deps.judge_llm = lambda m: judged.__setitem__("n", judged["n"] + 1) or _JUDGE
        deps.expert_llm = lambda m: expert.__setitem__("n", expert["n"] + 1) or _EXPL
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "квиз"])
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "А"})
        assert "Ошибка" in res.agent_message
        assert "Б" in res.agent_message          # правильный вариант показан без LLM
        assert judged["n"] == 0                   # судья не вызывался
        assert expert["n"] == 0                   # эксперт не вызывался

    def test_wrong_answer_triggers_explanation(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "квиз"])
        deps.eval_llm = lambda m: _EVAL_WRONG
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Неправильный ответ тут"})
        assert "Объяснение:" in res.agent_message
        assert "§12" in res.agent_message

    def test_records_filled_for_export(self, deps):
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "квиз"])
        # вопрос сгенерирован → в records появилась запись
        assert len(res.records) == 1
        assert res.records[0]["question_id"] == "q1"
        assert res.records[0]["topic"] == "Атмосфера"
        assert res.records[0]["student_answer"] is None  # ещё не отвечен

        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Атмосфера — воздушная оболочка Земли."})
        assert res.records[0]["student_answer"] == "Атмосфера — воздушная оболочка Земли."
        assert res.records[0]["score01"] == 0.8
        assert res.records[0]["correct"] is True
        assert res.records[0]["judge_score"] == 8.0
        assert res.records[0]["model_used"] == "tutor"

    def test_duplicate_question_regenerated(self, deps):
        """Антидубликат (7.3.2): второй вопрос, совпавший с первым по смыслу,
        регенерируется до отличного; в asked_questions — тексты."""
        calls = {"n": 0}

        def gen(m):
            calls["n"] += 1
            if calls["n"] <= 2:  # q1 и первый дубль q2
                return '{"question":"Что такое атмосфера?","options":null,"answer_type":"open","topic":"Атмосфера"}'
            return '{"question":"Из каких газов состоит атмосфера?","options":null,"answer_type":"open","topic":"Атмосфера"}'

        deps.tutor_llm = gen
        graph = build_graph(deps)
        state = TutorState(num_questions=2, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "квиз"])
        assert res.asked_questions == ["Что такое атмосфера?"]

        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Воздушная оболочка."})
        assert res.current_question is not None
        assert res.current_question.question == "Из каких газов состоит атмосфера?"
        assert res.asked_questions == ["Что такое атмосфера?", "Из каких газов состоит атмосфера?"]
        assert calls["n"] == 3  # q1 + q2-дубль + q2-регенерация

    def test_wiki_updated_on_quiz(self, deps, tmp_path):
        """Knowledge Wiki (roadmap #2): после квиза статья темы появляется на диске."""
        from src.wiki import KnowledgeWiki

        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "философия", "Кант", "нет", "квиз"])
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Кант — основоположник критической философии."})

        # мок-генератор _GEN создаёт вопрос с topic «Атмосфера» — статья создаётся по нему
        wiki = KnowledgeWiki(tmp_path / "wiki")
        articles = wiki.list_articles()
        assert len(articles) >= 1
        art = articles[0]
        assert art.attempts >= 1
        assert art.subject  # предмет из сессии сохранён

    def test_lesson_syncs_concepts_to_wiki(self, deps):
        """Roadmap #3: ключевые понятия урока попадают в wiki-статью темы (drill-down)."""
        from src.wiki import KnowledgeWiki

        def llm(messages):
            user = messages[-1]["content"] if messages else ""
            if "Контекст учебника" in user:
                return json.dumps({
                    "title": "Атмосфера",
                    "definition": "Атмосфера — газовая оболочка Земли.",
                    "key_terms": [{"term": "азот", "definition": "главный газ атмосферы"},
                                  {"term": "кислород", "definition": "21%"}],
                    "sections": [{"heading": "Состав", "body": "Азот и кислород — её основа."}],
                    "summary": "Итог.",
                })
            return _GEN

        deps.tutor_llm = llm
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "урок"])
        wiki = KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR)
        art = wiki.get("география", "Атмосфера")
        assert art is not None
        assert art.concepts == ["азот", "кислород"]

    def test_lesson_mode_before_quiz(self, deps):
        """Режим «урок»: структурированное объяснение темы → подтверждение → квиз."""
        def llm(messages):
            user = messages[-1]["content"] if messages else ""
            if "Контекст учебника" in user:
                return json.dumps({
                    "title": "Атмосфера",
                    "hook": "Почему самолёты летают в атмосфере?",
                    "definition": "Атмосфера — газовая оболочка Земли.",
                    "key_terms": [{"term": "азот", "definition": "главный газ атмосферы"}],
                    "diagram": {
                        "kind": "flow",
                        "title": "Состав атмосферы",
                        "nodes": [
                            {"id": "n1", "label": "Азот 78%"},
                            {"id": "n2", "label": "Кислород 21%"},
                        ],
                        "edges": [{"source": "n1", "target": "n2", "label": "смесь"}],
                    },
                    "sections": [
                        {"heading": "Состав", "body": "Азот и кислород — её основа.",
                         "citation": "§12", "check_question": "Какой газ преобладает?"},
                    ],
                    "summary": "Атмосфера защищает жизнь на Земле.",
                })
            return _GEN

        deps.tutor_llm = llm
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "урок"])
        assert res.mode == "lesson"
        assert res.lesson_done is True
        assert res.lesson_text and "газовая оболочка" in res.lesson_text
        # Структурированный урок: карточки вместо стены текста
        assert res.lesson_title == "Атмосфера"
        assert res.lesson_hook == "Почему самолёты летают в атмосфере?"
        assert res.lesson_sections and res.lesson_sections[0]["citation"] == "§12"
        assert res.lesson_sections[0]["check_question"] == "Какой газ преобладает?"
        assert res.lesson_key_terms == [{"term": "азот", "definition": "главный газ атмосферы"}]
        # Dual-coding: схема-иллюстрация к уроку (не противоречит секциям)
        assert res.lesson_diagram is not None
        assert res.lesson_diagram["kind"] == "flow"
        assert len(res.lesson_diagram["nodes"]) == 2
        assert res.lesson_diagram["edges"][0]["source"] == "n1"
        # LessonEval: детерминированный судья-lite посчитан без задержки
        assert res.lesson_eval is not None
        assert res.lesson_eval["verdict"] in ("pass", "fail")
        assert set(res.lesson_eval["criteria"]) == {"structure", "citations", "diagram", "readability", "length"}
        assert 0.0 <= res.lesson_eval["avg_score"] <= 1.0
        assert res.lesson_summary
        assert res.current_question is None  # квиз ещё не начался
        # «нет» → повтор урока (перегенерируется сразу, квиз не стартует)
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "нет"})
        assert res.lesson_done is True
        assert res.lesson_confirmed is False
        assert res.current_question is None
        assert res.lesson_text  # урок перегенерирован
        # «да» → переход к квизу
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "да"})
        assert res.lesson_confirmed is True
        assert res.current_question is not None

    def test_lesson_rag_gate_blocks_ungrounded_generation(self, deps):
        """RAG-first гейт: пустой контекст по теме → урок НЕ выдумывается из параметрических знаний."""
        deps.store.reset()  # коллекция пуста — релевантного контекста нет
        def llm(messages):
            user = messages[-1]["content"] if messages else ""
            if "Контекст учебника" in user:
                return json.dumps({"title": "Теплые течения", "sections": [{"body": "выдуманный контент"}]})
            return _GEN
        deps.tutor_llm = llm
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "Теплые течения", "нет", "урок"])
        assert res.lesson_text is None  # урок не сгенерирован
        assert res.lesson_done is False
        assert "нет материала" in (res.agent_message or "")
        # agent_question выставлен — CLI/веб не зацикливается на «внутреннем шаге»
        assert res.agent_question and "нет материала" in res.agent_question

    def test_explain_mode_shows_explanation(self, deps):
        """Режим «объяснение» (7.3.4): генерируется объяснение темы, затем подтверждение к квизу."""
        def llm(messages):
            system = messages[0]["content"] if messages else ""
            if "Объясни тему ученику" in system:
                return '{"text": "Атмосфера — газовая оболочка Земли, важнейший слой планеты."}'
            return _GEN

        deps.tutor_llm = llm
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "объяснение"])
        assert res.mode == "explain"
        assert res.lesson_done is True
        assert "оболочка" in res.lesson_text
        assert res.current_question is None
        assert res.agent_question  # подтверждение перехода к квизу
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "да"})
        assert res.current_question is not None

    def test_deep_dive_mode_uses_more_context(self, deps):
        """Режим «глубокий разбор» (7.3.4): синтез экспертной моделью, больше контекста."""
        def llm(messages):
            system = messages[0]["content"] if messages else ""
            if "ГЛУБОКИЙ РАЗБОР" in system:
                return '{"text": "Развёрнутый разбор атмосферы: понятия, связи, выводы.", "paragraphs": ["§12"]}'
            return _GEN

        deps.tutor_llm = llm
        deps.expert_llm = llm
        graph = build_graph(deps)
        state = TutorState(num_questions=1, sources=[{"type": "web", "url": "x"}], collection_id="web")
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "нет", "глубокий разбор"])
        assert res.mode == "deep_dive"
        assert "разбор" in res.lesson_text.lower()
        assert res.current_question is None
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "да"})
        assert res.current_question is not None


class TestRagFilterFallback:
    """Прогрессивное ослабление RAG-фильтра: класс не блокирует урок при пустом результате."""

    def test_grade_filter_relaxed_when_empty(self, deps):
        from src.graph import _rag_chunks

        # чанк в коллекции имеет grade="6", обучаемый — 7 класс → строгий фильтр пуст
        st = TutorState(subject="география", grade="7", topic="Атмосфера")
        chunks = _rag_chunks(deps.store, "Атмосфера", st, k=3)
        assert chunks
        assert "оболочка" in chunks[0].chunk.text  # fallback без grade вернул чанк

    def test_no_relaxation_when_filtered_match_exists(self, deps):
        from src.graph import _rag_chunks

        st = TutorState(subject="география", grade="6", topic="Атмосфера")
        chunks = _rag_chunks(deps.store, "Атмосфера", st, k=3)
        assert chunks and "оболочка" in chunks[0].chunk.text


class TestSourceFlow:
    def test_source_failed_path(self, deps, monkeypatch):
        class Failed:
            status = "failed"
            failed_reason = "empty_result"
            message = "Материалы по теме не найдены"
            sources = []
            texts = []

        monkeypatch.setattr(
            "src.graph.source_finder.collect_source_materials", lambda **kw: Failed()
        )
        graph = build_graph(deps)
        state = TutorState(num_questions=1)
        res = _feed(graph, state, ["студент", "физика", "Атомы", "нет", "квиз"])
        assert res.session_status == "failed"
        assert res.source_status == "failed"
        assert res.agent_message

    def test_source_failed_clears_stale_lesson(self, deps, monkeypatch):
        """Провал поиска не должен «всплывать» рядом с устаревшим уроком предыдущего запуска:
        session_status=failed + урок/оценка очищаются, чтобы в ленте не было противоречия."""
        class Failed:
            status = "failed"
            failed_reason = "empty_result"
            message = "Материалы по теме не найдены"
            sources = []
            texts = []

        monkeypatch.setattr(
            "src.graph.source_finder.collect_source_materials", lambda **kw: Failed()
        )
        graph = build_graph(deps)
        state = TutorState(
            num_questions=1, lesson_text="старый урок", lesson_title="Старая тема",
            lesson_eval={"verdict": "pass", "criteria": {}},
            lesson_judge=None,
        )
        res = _feed(graph, state, ["студент", "физика", "Атомы", "нет", "квиз"])
        assert res.session_status == "failed"
        assert res.lesson_text is None
        assert res.lesson_title is None
        assert res.lesson_eval is None
        assert res.lesson_judge is None

    def test_textbook_file_path(self, deps, tmp_path):
        doc = tmp_path / "doc.txt"
        doc.write_text(
            "Параграф 12: Атмосфера\nСтроение атмосферы.\n\nПараграф 13: Погода\nПогода меняется.",
            encoding="utf-8",
        )
        graph = build_graph(deps)
        state = TutorState(num_questions=1, textbook_file=str(doc))
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "да", "квиз"])
        # Уровень 1: конкретная тема «Атмосфера» нашлась среди уроков графа →
        # гейт пропущен, тема выбрана автоматически, квиз сразу
        assert res.source_status == "ready"
        assert res.awaiting_topic is False
        assert res.current_question is not None
        assert res.knowledge_graph is not None
        assert len(res.knowledge_graph["nodes"]) >= 3  # book + 2 параграфа

    def test_has_textbook_true_without_file_asks_upload(self, deps):
        """«да, есть учебник», но файл не загружен → просим загрузить, а не веб-поиск."""
        graph = build_graph(deps)
        state = TutorState(
            num_questions=1, learner_type="student", subject="география",
            topic="Атмосфера", has_textbook=True, mode="quiz",
        )
        res = _invoke(graph, state.model_dump())
        assert res.agent_question and "Загрузите" in res.agent_question
        assert res.source_status is None
        assert res.sources == []

    def test_index_failure_emits_source_failed(self, deps, tmp_path):
        """Сбой эмбеддингов при индексации → source_status=failed + событие source.failed."""
        doc = tmp_path / "doc.txt"
        doc.write_text("Параграф 12: Атмосфера\nСтроение атмосферы.", encoding="utf-8")
        events = []
        deps.on_event = lambda event, data: events.append(event)

        class BoomEmbedder:
            def encode(self, texts):
                raise RuntimeError("embeddings 503")

            def encode_query(self, text):
                raise RuntimeError("embeddings 503")

        deps.store.embedder = BoomEmbedder()
        graph = build_graph(deps)
        state = TutorState(num_questions=1, textbook_file=str(doc))
        res = _feed(graph, state, ["студент", "география", "Атмосфера", "да", "квиз"])
        assert res.source_status == "failed"
        assert res.session_status == "failed"
        assert "source.failed" in events
        assert res.agent_message and "индексировать" in res.agent_message


class TestBuild:
    def test_compiles_with_memory_checkpointer(self, deps):
        from langgraph.checkpoint.memory import MemorySaver

        graph = build_graph(deps, checkpointer=MemorySaver())
        res = _invoke(
            graph,
            TutorState().model_dump(),
            config={"configurable": {"thread_id": "t1"}},
        )
        assert res.intake_field == "learner_type"


def _minimal_scanned_pdf(tmp_path):
    """Минимальный PDF с коротким текстом «Hello PDF» → детектируется как скан."""
    body = b"BT\n/F1 24 Tf\n72 720 Td\n(Hello PDF) Tj\nET\n"
    content = f"<</Length {len(body)}>>\nstream\n".encode() + body + b"endstream\n"
    pdf = (
        b"%PDF-1.1\n"
        b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
        b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
        b"3 0 obj <</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources <</Font <</F1 5 0 R>>>>>> endobj\n"
        b"4 0 obj " + content + b"endobj\n"
        b"5 0 obj <</Type /Font /Subtype /Type1 /BaseFont /Helvetica>> endobj\n"
        b"trailer <</Root 1 0 R /Size 5>>\n%%EOF\n"
    )
    p = tmp_path / "scanned.pdf"
    p.write_bytes(pdf)
    return p


class TestScannedDoc:
    """Ветка «скан → запрос страниц+темы → OCR → индекс» (3.2)."""

    @pytest.fixture
    def scanned_deps(self, make_settings, tmp_path):
        s = make_settings(
            FGOS_REFERENCE_DIR=str(FGOS_DIR),
            TEXTBOOKS_DOWNLOADS_DIR=str(tmp_path / "downloads"),
            MAX_INTAKE_ITERATIONS=8,
            OCR_DETECT_PAGE_NUMBERS=False,
            OCR_MAX_ATTEMPTS=3,
            OCR_PAGE_BUFFER=3,
        )
        embedder = FakeEmbedder()
        store = NumpyVectorStore("scanned", embedder)
        return GraphDeps(
            embedder=embedder, store=store, settings=s,
            tutor_llm=lambda m: _GEN,
            eval_llm=lambda m: _EVAL_OK,
            expert_llm=lambda m: _EXPL,
            judge_llm=lambda m: _JUDGE,
        )

    def test_scanned_asks_pages_then_indexes(self, scanned_deps, tmp_path, monkeypatch):
        pdf = _minimal_scanned_pdf(tmp_path)
        monkeypatch.setattr(
            "src.knowledge.ocr_pages",
            lambda path, page_range, **kw: {
                "text": "Атмосфера — воздушная оболочка Земли, состоит из азота и кислорода. Много текста.",
                "pages": list(range(page_range[0], page_range[1] + 1)),
                "page_numbers": {},
                "offset": None,
            },
        )
        graph = build_graph(scanned_deps)
        state = TutorState(
            num_questions=1, textbook_file=str(pdf),
            learner_type="student", subject="география", topic="Атмосфера",
            has_textbook=True, mode="quiz",
        )
        # 1) распознан скан → агент просит страницы+тему
        res = _invoke(graph, state.model_dump())
        assert res.textbook_scanned is True
        assert res.agent_question and "страниц" in res.agent_question

        # 2) ответ «1-1, Атмосфера» → OCR(мок) → индекс → граф; тема конкретна → квиз сразу
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "1-1, Атмосфера"})
        assert res.textbook_pages == "1-1, Атмосфера"
        assert res.source_status == "ready"
        assert res.collection_id == "ocr"
        assert res.awaiting_topic is False
        assert res.current_question is not None
        assert res.agent_question

    def test_scanned_retry_then_all(self, scanned_deps, tmp_path, monkeypatch):
        pdf = _minimal_scanned_pdf(tmp_path)
        monkeypatch.setattr(
            "src.knowledge.ocr_pages",
            lambda path, page_range, **kw: {
                "text": "Атмосфера — воздушная оболочка. Текст для проверки темы.",
                "pages": list(range(page_range[0], page_range[1] + 1)),
                "page_numbers": {},
                "offset": None,
            },
        )
        graph = build_graph(scanned_deps)
        state = TutorState(
            num_questions=1, textbook_file=str(pdf),
            learner_type="student", subject="география", topic="Атмосфера",
            has_textbook=True, mode="quiz",
        )
        res = _invoke(graph, state.model_dump())
        # «не знаю» → переспрос
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "не знаю"})
        assert res.doc_pages_attempts == 1
        assert res.agent_question and "открой" in res.agent_question
        # «все» → полный OCR → индекс; тема «Атмосфера» конкретна → квиз сразу
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "все"})
        assert res.source_status == "ready"
        assert res.awaiting_topic is False
        assert res.current_question is not None


class TestWebSourceFlow:
    """Студент без учебника: find_textbook → веб-материалы → логичный граф (roadmap: «лепнина»)."""

    @pytest.fixture
    def web_deps(self, make_settings, tmp_path):
        s = make_settings(
            FGOS_REFERENCE_DIR=str(FGOS_DIR),
            TEXTBOOKS_DOWNLOADS_DIR=str(tmp_path / "downloads"),
            MAX_INTAKE_ITERATIONS=8,
            OCR_MIN_TEXT_CHARS=20,
            KNOWLEDGE_GRAPH_DIR=str(tmp_path / "kg"),
            KNOWLEDGE_WIKI_DIR=str(tmp_path / "wiki"),
        )
        embedder = FakeEmbedder()
        store = NumpyVectorStore("web", embedder)

        def fake_collector(**kw):
            from src.source_finder import SourceCollection

            return SourceCollection(
                status="ready",
                sources=[
                    {"type": "page", "url": "https://ru.wikipedia.org/wiki/Кант", "license": "ok"},
                    {"type": "page", "url": "https://ru.wikibooks.org/wiki/Философия", "license": "ok"},
                ],
                texts=[
                    "# Иммануил Кант\n## Жизнь и биография\nТекст.\n## Критика чистого разума\nТекст.\n## Примечания\nМусор.\n",
                    "# Философия\n## Категорический императив\nТекст.\n## См. также\nМусор.\n",
                ],
                message="Собрано 2 источника",
            )

        return GraphDeps(
            embedder=embedder, store=store, settings=s,
            source_collector=fake_collector,
            tutor_llm=lambda m: _GEN,
            eval_llm=lambda m: _EVAL_OK,
            expert_llm=lambda m: _EXPL,
            judge_llm=lambda m: _JUDGE,
        )

    def test_graph_built_per_source_no_noise(self, web_deps):
        """Граф: корень + узлы-источники + их подтемы; шумовые секции отсекаются."""
        graph = build_graph(web_deps)
        state = TutorState(num_questions=3)
        res = _feed(graph, state, ["студент", "философия", "Кант", "нет", "квиз"])
        assert res.source_status == "ready"
        # Уровень 1: тема «Кант» конкретна → гейт пропущен, квиз сразу
        assert res.awaiting_topic is False
        assert res.current_question is not None

        kg = res.knowledge_graph or {}
        titles = [n["title"].lower() for n in kg.get("nodes", [])]
        # логичные темы присутствуют
        assert "жизнь и биография" in titles
        assert "критика чистого разума" in titles
        assert "категорический императив" in titles
        # шум не попал в узлы
        assert not any("примечания" in t for t in titles)
        assert not any("см. также" in t for t in titles)
        # узлы-источники присутствуют
        assert any("wikipedia" in t for t in titles)
        # корень-книга присутствует
        node_types = {n["type"] for n in kg.get("nodes", [])}
        assert "book" in node_types

    def test_upload_recovers_from_failed_search(self, deps, tmp_path):
        """После провала поиска (session_status=failed) загрузка учебника перезапускает квиз
        (без этого route_tutor_agent уводит в сводку вместо квиза)."""
        doc = tmp_path / "doc.txt"
        doc.write_text(
            "Параграф 12: Атмосфера\nСтроение атмосферы.\n\nПараграф 13: Погода\nПогода меняется.",
            encoding="utf-8",
        )
        graph = build_graph(deps)
        state = TutorState(
            num_questions=1, textbook_file=str(doc), has_textbook=True,
            learner_type="student", subject="география", topic="Атмосфера", mode="quiz",
            session_status="failed", source_status=None,
        )
        res = _invoke(graph, state.model_dump())
        assert res.session_status != "failed"
        assert res.source_status == "ready"
        assert res.current_question is not None

    def test_web_topics_get_parent_id(self, web_deps):
        """Иерархия веб-графа: подтемы страницы → parent_id=страница (для группировки чипов)."""
        graph = build_graph(web_deps)
        state = TutorState(num_questions=3)
        res = _feed(graph, state, ["студент", "философия", "Кант", "нет", "квиз"])
        kg = res.knowledge_graph or {}
        nodes = {n["id"]: n for n in kg.get("nodes", [])}
        pages = [n for n in nodes.values() if str(n.get("parent_id", "")).startswith("book:")]
        assert pages, "узлы-источники должны иметь parent_id=root"
        page_id = pages[0]["id"]
        children = [n for n in nodes.values() if n.get("parent_id") == page_id]
        assert children, "подтемы страницы должны иметь parent_id=страница"
        assert all(n["type"] == "topic" for n in children)

    def test_student_no_textbook_concrete_topic_skips_gate(self, web_deps):
        """Уровень 1: конкретная тема в intake → после сбора материалов гейт не нужен."""
        graph = build_graph(web_deps)
        state = TutorState(num_questions=3)
        res = _feed(graph, state, ["студент", "философия", "Кант", "нет", "квиз"])
        assert res.source_status == "ready"
        assert res.awaiting_topic is False
        assert res.current_question is not None  # квиз начался без переспроса темы

    def test_all_topic_goes_to_gate(self, web_deps):
        """Тема «все» → нужен выбор темы из графа (гейт остаётся)."""
        graph = build_graph(web_deps)
        state = TutorState(num_questions=3)
        res = _feed(graph, state, ["студент", "философия", "все", "нет", "квиз"])
        assert res.source_status == "ready"
        assert res.awaiting_topic is True
        assert res.current_question is None
        # выбор темы из графа → квиз
        res = _invoke(graph, {**res.model_dump(), "pending_answer": "Критика чистого разума"})
        assert res.awaiting_topic is False
        assert res.current_question is not None

    def test_intent_message_emitted_before_search(self, web_deps):
        """Уровень 3: подтверждение намерения (режим+тема) эмитится перед поиском."""
        events = []
        web_deps.on_event = lambda event, data: events.append((event, data))
        graph = build_graph(web_deps)
        state = TutorState(num_questions=3)
        res = _feed(graph, state, ["студент", "философия", "Кант", "нет", "квиз"])
        intents = [d.get("message") for ev, d in events if ev == "system" and d.get("kind") == "intent"]
        assert intents, "intent-сообщение не отправлено"
        assert any("квиз" in m and "Кант" in m for m in intents)


class TestTopicKeyConsistency:
    """Единый ключ темы (title узла) между графом, knowledge_map и wiki:
    иначе мастерство не окрашивает узлы графа (см. KnowledgeGraphPanel)."""

    def _kg(self):
        from src.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        kg.add_topic("book:g", "Учебник «география»", node_type="book")
        kg.add_topic("sec:g:12", "Урок 12: Атмосфера", node_type="lesson")
        kg.add_edge("book:g", "sec:g:12", "part_of")
        return kg

    def test_topic_gate_sets_topic_to_node_title(self, deps):
        """Выбор темы текстом в topic_gate → st.topic = название узла (не широкий ввод)."""
        graph = build_graph(deps)
        state = TutorState(
            num_questions=1, learner_type="student", subject="география",
            topic="Атмосфера", mode="quiz", has_textbook=False,
            knowledge_graph=self._kg().to_dict(), awaiting_topic=True,
            source_status="ready",
        )
        res = _invoke(graph, {**state.model_dump(), "pending_answer": "Урок 12"})
        assert res.active_topic == "sec:g:12"
        assert res.topic == "Урок 12: Атмосфера"

    def test_generate_question_uses_active_topic_title(self, deps):
        """Вопрос по активному узлу генерится по title узла (а не широкой теме)."""
        from src.graph import generate_question_node

        events = []
        deps.on_event = lambda event, data: events.append((event, data))
        state = TutorState(
            num_questions=1, learner_type="student", subject="география",
            topic="география", mode="quiz",
            knowledge_graph=self._kg().to_dict(), active_topic="sec:g:12",
        )
        res = TutorState.model_validate(generate_question_node(state, deps))
        progress = [d.get("message") for ev, d in events if ev == "source.progress"]
        assert any("Урок 12: Атмосфера" in m for m in progress), progress
        assert res.current_question is not None

    def test_record_has_correct_answer_for_note(self, deps):
        """Record сохраняет эталонные ответы (correct_answer) — для полной заметки об ошибке."""
        from src.graph import generate_question_node

        deps.tutor_llm = lambda m: ('{"question": "Что такое атмосфера?", "options": null, '
                                    '"answer_type": "open", "topic": "Атмосфера", '
                                    '"correct_answers": ["Атмосфера — газовый слой"]}')
        state = TutorState(num_questions=1, learner_type="student", subject="география",
                           topic="Атмосфера", mode="quiz")
        res = TutorState.model_validate(generate_question_node(state, deps))
        assert res.records and res.records[-1]["correct_answer"] == "Атмосфера — газовый слой"
        assert res.records[-1]["question"] == "Что такое атмосфера?"


class TestWikiIndexExtraction:
    """Roadmap #2 (Wiki-LLM): индекс-время извлечение фактов в wiki-статьи из графа."""

    def _kg(self):
        from src.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        kg.add_topic("book:g", "Учебник «география»", node_type="book")
        kg.add_topic("sec:g:12", "Урок 12: Атмосфера", node_type="section", section_number="12")
        kg.add_edge("book:g", "sec:g:12", "part_of")
        return kg

    def test_extract_creates_articles_and_emits(self, deps):
        from src.graph import _wiki_extract_from_graph
        from src.wiki import KnowledgeWiki

        st = TutorState(learner_type="student", subject="география", grade="6",
                        knowledge_graph=self._kg().to_dict())
        llm_call = lambda msgs: "Атмосфера — воздушная оболочка Земли, состоит из азота и кислорода."
        events = []
        deps.on_event = lambda event, data: events.append((event, data))
        _wiki_extract_from_graph(st, deps, limit=5, llm_call=llm_call)

        wiki = KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR)
        art = wiki.get("география", "Урок 12: Атмосфера")
        assert art is not None
        assert "воздушная оболочка" in art.body
        assert any(ev == "wiki.updated" for ev, _ in events)

        # идемпотентность: повторный вызов не перезаписывает статью
        _wiki_extract_from_graph(st, deps, limit=5, llm_call=llm_call)
        art2 = wiki.get("география", "Урок 12: Атмосфера")
        assert art2.body == art.body

    def test_extract_caps_batch(self, deps):
        from src.graph import _wiki_extract_from_graph
        from src.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        kg.add_topic("book:g", "Учебник «география»", node_type="book")
        for i in range(6):
            kg.add_topic(f"sec:g:{i}", f"Тема {i}", node_type="topic", section_number=str(i))
            kg.add_edge("book:g", f"sec:g:{i}", "part_of")
        st = TutorState(learner_type="student", subject="география",
                        knowledge_graph=kg.to_dict())
        calls = {"n": 0}

        def llm_call(msgs):
            calls["n"] += 1
            return "Текст конспекта темы по контексту учебника, достаточно длинный."

        _wiki_extract_from_graph(st, deps, limit=3, llm_call=llm_call)
        assert calls["n"] == 3  # кап сработал
