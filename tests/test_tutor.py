"""Тесты тьюторинг-цикла (Слайс 6): генерация, оценка, адаптация, knowledge_map."""

from __future__ import annotations

import json

import pytest

from src.config import BASE_DIR, Settings
from src.states import TutorState
from src.tutor import (
    adjust_difficulty,
    difficulty_for_grade,
    evaluate_answer,
    explain_error,
    generate_lesson,
    generate_question,
    grade_prompt,
    is_duplicate_question,
    parse_llm_json,
    simplicity_precheck,
    update_knowledge_map,
)


class _SimpleEmbedder:
    """Хэш-эмбеддер (md5, 16 измерений): одинаковый текст → одинаковый вектор."""

    def _vec(self, text):
        import hashlib

        v = [0.0] * 16
        for token in (text or "").lower().split():
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:4], 16)
            v[h % 16] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def encode(self, texts):
        return [self._vec(t) for t in texts]

    def encode_query(self, text):
        return self._vec(text)


class TestDuplicateQuestion:
    def test_identical_question_is_duplicate(self):
        emb = _SimpleEmbedder()
        assert is_duplicate_question(emb, "Что такое атмосфера?", ["Что такое атмосфера?"]) is True

    def test_similar_question_detected(self):
        emb = _SimpleEmbedder()
        prev = ["Сколько процентов азота в атмосфере?"]
        assert is_duplicate_question(emb, "Каково содержание азота в атмосфере?", prev, threshold=0.3) is True

    def test_distinct_question_not_duplicate(self):
        emb = _SimpleEmbedder()
        prev = ["Что такое атмосфера?"]
        assert is_duplicate_question(emb, "Перечисли слои земной коры.", prev) is False

    def test_empty_prev_or_question(self):
        emb = _SimpleEmbedder()
        assert is_duplicate_question(emb, "Вопрос?", []) is False
        assert is_duplicate_question(emb, "", ["Вопрос?"]) is False

    def test_embedder_failure_does_not_block(self):
        class Broken:
            def encode_query(self, _):
                raise RuntimeError("no model")

            def encode(self, _):
                raise RuntimeError("no model")

        assert is_duplicate_question(Broken(), "Вопрос?", ["Вопрос?"]) is False


class TestGradePrompt:
    def test_grade6_easy(self):
        assert "5-6" in grade_prompt("6")
        assert "простые" in grade_prompt("6")

    def test_grade8_medium(self):
        assert "7-9" in grade_prompt("8")

    def test_grade10_hard(self):
        assert "10-11" in grade_prompt("11")

    def test_student(self):
        assert "студент" in grade_prompt(None)

    def test_difficulty_for_grade(self):
        assert difficulty_for_grade("6") == "easy"
        assert difficulty_for_grade("8") == "medium"
        assert difficulty_for_grade("11") == "hard"
        assert difficulty_for_grade(None) == "medium"


class TestParseLlmJson:
    def test_plain(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert parse_llm_json('```json\n{"a": 2}\n```') == {"a": 2}

    def test_with_surrounding_text(self):
        assert parse_llm_json('Вот ответ: {"a": 3} готово') == {"a": 3}

    def test_invalid_returns_empty(self):
        assert parse_llm_json("не json совсем") == {}
        assert parse_llm_json("") == {}


def _state(**kw) -> TutorState:
    return TutorState(learner_type="student", subject="география", has_textbook=False, mode="quiz", **kw)


class TestGenerateQuestion:
    def test_valid_card_from_llm(self):
        state = _state(grade="6", curriculum="ФГОС")
        fake = lambda msgs: '{"question": "Что такое атмосфера?", "options": ["газ", "жидкость"], "answer_type": "single", "topic": "Атмосфера", "correct_answers": ["газ"]}'
        card = generate_question("Атмосфера", ["Атмосфера — оболочка Земли."], "easy", state, llm_call=fake)
        assert card.question_id == "q1"
        assert card.answer_type == "single"
        assert card.difficulty == "easy"
        assert card.topic == "Атмосфера"
        assert state.asked_questions == ["Что такое атмосфера?"]  # тексты для антидубликата
        assert state.current_question is card
        # эталонные ответы LLM хранятся в состоянии, но НЕ в QuizCard (не утекают в UI)
        assert state.current_answers == ["газ"]
        assert card.model_dump().get("correct_answers") is None

    def test_fallback_when_llm_garbage(self):
        state = _state()
        card = generate_question("Атмосфера", ["текст"], "medium", state, llm_call=lambda m: "не json")
        assert card.answer_type == "open"
        assert "Атмосфера" in card.question

    def test_open_question_options_none(self):
        state = _state()
        fake = lambda m: '{"question": "Опишите", "options": null, "answer_type": "open", "topic": "Тема"}'
        card = generate_question("Тема", ["контекст"], "hard", state, llm_call=fake)
        assert card.options is None


class TestSimplicityPrecheck:
    def test_short_answer_rejected(self):
        assert simplicity_precheck("да", ["Атмосфера — воздушная оболочка Земли."]) is False
        assert simplicity_precheck("", ["Атмосфера — воздушная оболочка Земли."]) is False

    def test_gibberish_rejected(self):
        # «x»*100 — одна «буква», реального содержания нет
        assert simplicity_precheck("x" * 100, ["Атмосфера — воздушная оболочка Земли."]) is False

    def test_paraphrase_accepted(self):
        # Хороший ответ своими словами (без совпадения терминов чанка) — НЕ режем
        answer = "Помогать тому, кому самому было бы приятно получить поддержку."
        assert simplicity_precheck(answer, ["поступай с другими так, как хочешь, чтобы поступали с тобой"]) is True

    def test_long_with_term_accepted(self):
        answer = "Атмосфера — это воздушная оболочка Земли, и она важна."
        assert simplicity_precheck(answer, ["Атмосфера — воздушная оболочка Земли."]) is True


class TestEvaluateAnswer:
    def test_precheck_fail_short(self):
        state = _state()
        graded = evaluate_answer("Вопрос", "да", ["Атмосфера — воздушная оболочка."], state)
        assert graded.precheck_passed is False
        assert graded.model_used == "rule-based"
        assert graded.correct is False

    def test_llm_evaluation(self):
        state = _state()
        fake = lambda m: '{"score": 8, "correct": true, "feedback": "Верно!", "citation_ok": true}'
        answer = "Атмосфера — это воздушная оболочка Земли."
        graded = evaluate_answer("Вопрос", answer, ["Атмосфера — воздушная оболочка."], state, llm_call=fake)
        assert graded.precheck_passed is True
        assert graded.score == pytest.approx(0.8)
        assert graded.correct is True
        assert graded.citation_ok is True

    def test_expert_for_long_answer(self):
        state = _state()
        fake = lambda m: '{"score": 9, "correct": true, "feedback": "ok", "citation_ok": false}'
        long_answer = "Атмосфера — это воздушная оболочка, " + "и рассуждение " * 200
        graded = evaluate_answer("Вопрос", long_answer, ["Атмосфера — воздушная оболочка."], state, llm_call=fake)
        assert graded.model_used == "expert"  # Ж-8: развёрнутый ответ → эксперт

    def test_reference_match_marks_correct(self):
        """Закрытый вопрос: ответ ученика совпал с эталоном LLM → верно без LLM-вызова."""
        state = _state()
        fake_gen = lambda m: '{"question":"Вопрос","options":["А","Б"],"answer_type":"single","topic":"Тема","correct_answers":["Б"]}'
        card = generate_question("Тема", ["контекст"], "medium", state, llm_call=fake_gen)
        graded = evaluate_answer(card.question, "Б", ["контекст"], state)
        assert graded.correct is True
        assert graded.model_used == "reference"
        assert graded.score == 1.0

    def test_closed_no_match_is_deterministic_wrong(self):
        """Закрытый вопрос, выбран неверный вариант → «Ошибка» без LLM, правильный в feedback."""
        state = _state()
        fake_gen = lambda m: '{"question":"Вопрос","options":["А","Б"],"answer_type":"single","topic":"Тема","correct_answers":["Б"]}'
        card = generate_question("Тема", ["контекст"], "medium", state, llm_call=fake_gen)
        sent = {}

        def fake_eval(messages):
            sent["called"] = True
            return '{"score": 0, "correct": false, "feedback": "x", "citation_ok": false}'

        graded = evaluate_answer(card.question, "А", ["контекст"], state, llm_call=fake_eval)
        assert graded.correct is False
        assert graded.model_used == "reference"
        assert "Б" in graded.feedback  # правильный ответ показан без LLM
        assert "called" not in sent     # LLM не вызывался

    def test_generate_lesson_json(self):
        state = _state()
        fake = lambda m: '{"text": "Атмосфера — газовая оболочка Земли. Она защищает планету."}'
        lesson = generate_lesson("Атмосфера", ["контекст"], state, llm_call=fake)
        text = lesson.render_text()
        assert "газовая оболочка" in text
        assert len(text) > 30

    def test_generate_lesson_structured(self):
        """Урок генерируется по LessonSchema: hook/definition/термины/секции с цитатой."""
        state = _state()
        fake = lambda m: json.dumps({
            "title": "Атмосфера",
            "hook": "Почему небо голубое?",
            "definition": "Атмосфера — газовая оболочка Земли.",
            "key_terms": [{"term": "атмосфера", "definition": "воздушная оболочка планеты"}],
            "sections": [
                {"heading": "Состав", "body": "В атмосфере есть азот и кислород.",
                 "citation": "§12", "check_question": "Назови два главных газа."},
                {"heading": "Роль", "body": "Атмосфера защищает от радиации.",
                 "citation": "", "check_question": ""},
            ],
            "summary": "Атмосфера — защитный слой Земли.",
        })
        lesson = generate_lesson("Атмосфера", ["контекст"], state, llm_call=fake)
        assert lesson.hook == "Почему небо голубое?"
        assert lesson.definition.startswith("Атмосфера")
        assert lesson.key_terms == [{"term": "атмосфера", "definition": "воздушная оболочка планеты"}]
        assert len(lesson.sections) == 2
        assert lesson.sections[0].heading == "Состав"
        assert lesson.sections[0].citation == "§12"
        assert lesson.sections[0].check_question == "Назови два главных газа."
        assert lesson.sections[1].check_question == ""  # пустые поля остаются пустыми

    def test_generate_lesson_cleans_nested_json_definition(self):
        """Модель вложила весь урок JSON строкой в «definition» — в UI не должен
        попасть сырой JSON (баг: вместо карточки урока показывался JSON)."""
        state = _state()
        nested = {
            "title": "Поэты серебряного века",
            "hook": "Как поэты изменили литературу?",
            "definition": "Серебряный век — расцвет поэзии.",
            "key_terms": [{"term": "Акмеизм", "definition": "направление"}],
            "sections": [{"heading": "Новые направления", "body": "Поэты искали новые пути."}],
            "summary": "Итог.",
        }
        fake = lambda m: json.dumps({
            "title": nested["title"],
            "hook": nested["hook"],
            # «вложенность»: модель дублирует весь объект строкой в первое поле
            "definition": json.dumps(nested, ensure_ascii=False),
            "key_terms": nested["key_terms"],
            "sections": nested["sections"],
            "summary": nested["summary"],
        })
        lesson = generate_lesson("Поэты серебряного века", ["контекст"], state, llm_call=fake)
        assert "title" not in (lesson.definition or "").lower()  # сырой JSON вычищен
        assert not (lesson.definition or "").startswith("{")
        assert lesson.hook == nested["hook"]  # остальные поля корректны
        assert lesson.sections[0].heading == "Новые направления"

    def test_generate_lesson_cleans_nested_dict_definition(self):
        """То же, но поле — настоящий dict (не строка): определение очищается."""
        state = _state()
        fake = lambda m: json.dumps({
            "title": "Тема",
            "hook": "Вопрос?",
            "definition": {"title": "Тема", "sections": []},
            "key_terms": [],
            "sections": [{"heading": "Раздел", "body": "Текст раздела."}],
        })
        lesson = generate_lesson("Тема", ["контекст"], state, llm_call=fake)
        assert lesson.definition == ""

    def test_generate_lesson_cleans_bom_quoted_json_definition(self):
        """Вложенный JSON с BOM и кавычками тоже очищается."""
        state = _state()
        nested = json.dumps({"title": "Тема", "sections": []}, ensure_ascii=False)
        fake = lambda m: json.dumps({
            "title": "Тема",
            "hook": "Вопрос?",
            "definition": "\ufeff" + "'" + nested + "'",  # BOM + кавычки-обёртка
            "key_terms": [],
            "sections": [{"heading": "Раздел", "body": "Текст раздела."}],
        })
        lesson = generate_lesson("Тема", ["контекст"], state, llm_call=fake)
        assert lesson.definition == ""
        assert lesson.sections[0].heading == "Раздел"

    def test_generate_lesson_unparsable_json_falls_back_to_context(self):
        """Нераспарсившийся JSON (сломанная структура) не попадает в урок —
        используется контекст, иначе в UI появилась бы «стена» из JSON."""
        state = _state()
        broken_json = '{"title": "Тема", "sections": [{"heading": "Раздел", "body": "Текст"}'  # невалидный
        lesson = generate_lesson("Тема", ["Контекст: Атмосфера — газовая оболочка."], state, llm_call=lambda m: broken_json)
        rendered = lesson.render_text()
        assert "title" not in rendered.lower()
        assert "sections" not in rendered.lower()
        assert "Контекст" in rendered  # fallback на контекст

    def test_generate_lesson_diagram_map(self):
        """Map-диаграмма с координатами и цветами течений; санитизация."""
        state = _state(grade="7")
        fake = lambda m: json.dumps({
            "title": "Теплые течения",
            "definition": "Тёплые течения — потоки тёплой воды.",
            "diagram": {
                "kind": "map",
                "title": "Гольфстрим",
                "nodes": [
                    {"id": "e", "label": "Европа", "x": 0.8, "y": 0.3},
                    {"id": "a", "label": "Америка", "x": 0.2, "y": 0.4},
                    {"id": "g", "label": "Гольфстрим", "x": 0.5, "y": 0.6},
                    {"id": "z", "label": "", "x": 5, "y": 5},
                ],
                "edges": [
                    {"source": "a", "target": "g", "label": "тёплое", "color": "warm"},
                    {"source": "g", "target": "e", "color": "warm"},
                    {"source": "nope", "target": "e"},  # нет такого узла — отбрасывается
                ],
            },
            "sections": [{"body": "Гольфстрим тёплый."}],
        })
        lesson = generate_lesson("Теплые течения", ["контекст"], state, llm_call=fake)
        diag = lesson.diagram
        assert diag is not None
        assert diag.kind == "map"
        # пустой узел отброшен, координаты клампированы
        assert {n.id for n in diag.nodes} == {"e", "a", "g"}
        assert diag.nodes[0].x == 0.8
        # ссылка на несуществующий узел отброшена
        assert len(diag.edges) == 2
        assert diag.edges[0].color == "warm"


class TestLessonQualityGate:
    def _lesson(self, **kw) -> Lesson:
        from api.schemas import Lesson

        return Lesson(**kw)

    def test_accepts_structured_lesson(self):
        from src.tutor import lesson_quality_ok

        ok, reason = lesson_quality_ok(self._lesson(
            title="Атмосфера",
            definition="Атмосфера — газовая оболочка Земли.",
            sections=[{"heading": "Состав", "body": "В атмосфере есть азот и кислород."}],
        ))
        assert ok, reason

    def test_rejects_short_definition_only(self):
        """Секции вычистились в пустоту, определение — короткий скрап-заголовок."""
        from src.tutor import lesson_quality_ok

        ok, reason = lesson_quality_ok(self._lesson(
            title="Атмосфера",
            definition="Слои атмосферы — урок. География, 6 класс. Вход",
        ))
        assert not ok
        assert reason in ("no_content", "definition_only_shallow", "slideshow_chrome")

    def test_accepts_long_definition_only(self):
        """Определение — содержательное предложение, даже без секций."""
        from src.tutor import lesson_quality_ok

        long_def = ("Атмосфера — воздушная оболочка Земли, которая защищает планету "
                    "от солнечной радиации и метеоритов.")
        ok, reason = lesson_quality_ok(self._lesson(
            title="Атмосфера",
            definition=long_def,
        ))
        assert ok, reason

    def test_rejects_portal_title_chrome_in_definition(self):
        from src.tutor import lesson_quality_ok

        ok, reason = lesson_quality_ok(self._lesson(
            title="Поэты серебряного века",
            definition="Поэты Серебряного Века — презентация онлайн",
            sections=[{"body": "Символизм"}, {"body": "Акмеизм"}],
        ))
        assert not ok
        assert reason == "slideshow_chrome"

    def test_rejects_slideshow_chrome_in_definition(self):
        from src.tutor import lesson_quality_ok

        ok, reason = lesson_quality_ok(self._lesson(
            title="Поэты серебряного века",
            definition="Поэты Серебряного Века - презентация онлайн",
            sections=[{"body": "Символизм"}, {"body": "Акмеизм"}],
        ))
        assert not ok
        assert reason == "slideshow_chrome"

    def test_rejects_fragment_echo(self):
        """«Выплюнутый» контекст: короткие секции без заголовков — не урок."""
        from src.tutor import lesson_quality_ok

        ok, reason = lesson_quality_ok(self._lesson(
            title="Поэты серебряного века",
            definition="Серебряный век — период в русской культуре.",
            sections=[{"body": "Символизм"}, {"body": "Акмеизм"},
                      {"body": "Футуризм"}, {"body": "Имажинизм"}],
        ))
        assert not ok
        assert reason == "fragments"

    def test_rejects_no_content(self):
        from src.tutor import lesson_quality_ok

        ok, reason = lesson_quality_ok(self._lesson(title="Тема"))
        assert not ok
        assert reason == "no_content"

    def test_accepts_long_headless_sections(self):
        """Связные абзацы без заголовков (repair из текста) — принимаются."""
        from src.tutor import lesson_quality_ok

        long = "Серебряный век — период в истории русской культуры с 1890-х по начало 1920-х годов."
        ok, reason = lesson_quality_ok(self._lesson(
            title="Поэты серебряного века",
            definition=long,
            sections=[{"body": long}, {"body": long}],
        ))
        assert ok, reason

    def test_generate_lesson_diagram_bad_kind_defaults(self):
        """Неизвестный kind → flow; битая диаграмма → None (не роняет урок)."""
        state = _state()
        fake = lambda m: json.dumps({
            "definition": "Атмосфера — оболочка.",
            "diagram": {"kind": "spider", "nodes": [{"id": "n", "label": "x"}]},
            "sections": [{"body": "текст"}],
        })
        lesson = generate_lesson("Атмосфера", ["контекст"], state, llm_call=fake)
        assert lesson.diagram.kind == "flow"
        fake2 = lambda m: json.dumps({
            "definition": "Атмосфера — оболочка.",
            "diagram": {"kind": "flow", "nodes": []},  # пусто → диаграммы нет
            "sections": [{"body": "текст"}],
        })
        lesson2 = generate_lesson("Атмосфера", ["контекст"], state, llm_call=fake2)
        assert lesson2.diagram is None
        assert lesson2.sections[0].body == "текст"

    def test_generate_lesson_repair_plain_text(self):
        """LLM вернул сплошной текст (не JSON) — repair собирает урок из секций."""
        state = _state()
        fake = lambda m: (
            "Атмосфера — газовый слой планеты.\n"
            "Она состоит из азота.\n"
            "Кислород нужен для дыхания.\n"
            "Итог: атмосфера защищает жизнь."
        )
        lesson = generate_lesson("Атмосфера", ["контекст"], state, llm_call=fake)
        assert lesson.definition.startswith("Атмосфера")
        assert len(lesson.sections) >= 2
        assert any("Кислород" in s.body for s in lesson.sections)
        assert lesson.summary
        # контент не потерян
        assert "защищает жизнь" in lesson.render_text()

    def test_generate_lesson_fallback_on_garbage(self):
        state = _state()
        fake = lambda m: "не json вообще"
        lesson = generate_lesson("Атмосфера", ["Атмосфера — воздушная оболочка Земли."], state, llm_call=fake)
        assert "воздушная оболочка" in lesson.render_text()
        # фолбэк — односекционный урок, структура не ломается
        assert len(lesson.sections) == 1
        assert "воздушная оболочка" in lesson.sections[0].body

    def test_generate_lesson_with_on_token_mock(self):
        """on_token (стриминг) + мок-llm_call: при on_token используется chat_stream (токены уходят в callback)."""
        state = _state()
        seen = []
        # При заданном on_token всегда используется chat_stream, llm_call игнорируется
        lesson = generate_lesson("Атмосфера", ["контекст"], state, on_token=lambda t: seen.append(t))
        # Текста из мок-LLM не будет — fallback на контекст
        assert len(lesson.render_text()) > 0

    def test_generate_text_returns_llm_output(self):
        from src.tutor import generate_text

        text = generate_text([{"role": "user", "content": "x"}], llm_call=lambda m: "ответ")
        assert text == "ответ"


class TestOfflineFallback:
    """Офлайн-сценарии (нет интернета/AI-сервиса): LLM недоступен → template-fallback."""

    def _boom(self, *a, **k):
        raise RuntimeError("Все провайдеры и модели недоступны: offline")

    def _patch_llm_offline(self, monkeypatch):
        from src.llm_client import LLMClient

        monkeypatch.setattr(LLMClient, "chat", self._boom)
        monkeypatch.setattr(LLMClient, "chat_stream", self._boom)

    def test_generate_lesson_llm_offline_template(self, monkeypatch):
        self._patch_llm_offline(monkeypatch)
        state = _state(grade="6")
        lesson = generate_lesson(
            "Атмосфера",
            ["Атмосфера — воздушная оболочка Земли, состоит из азота (78%) и кислорода (21%)."],
            state,
        )
        text = lesson.render_text()
        assert len(text) > 40
        assert "Атмосфера" in text

    def test_generate_question_llm_offline_template(self, monkeypatch):
        self._patch_llm_offline(monkeypatch)
        state = _state()
        card = generate_question("Атмосфера", ["Атмосфера — воздушная оболочка."], "medium", state)
        assert card.answer_type == "open"
        assert "Атмосфера" in card.question

    def test_evaluate_answer_llm_offline_heuristic(self, monkeypatch):
        from src.llm_client import LLMClient

        state = _state(grade="6")
        generate_question(
            "Атмосфера", ["Атмосфера — воздушная оболочка Земли."], "easy", state,
            llm_call=lambda m: '{"question": "Что такое атмосфера?", "options": null, '
                               '"answer_type": "open", "topic": "Атмосфера", '
                               '"correct_answers": ["воздушная оболочка"]}',
        )
        monkeypatch.setattr(LLMClient, "chat", self._boom)
        graded = evaluate_answer(
            state.current_question.question,
            "Атмосфера — воздушная оболочка Земли",
            ["Атмосфера — воздушная оболочка Земли."],
            state,
        )
        assert graded.model_used == "rule-based"
        assert graded.correct is True
        assert graded.score == 0.7

    def test_explain_error_llm_offline_template(self, monkeypatch):
        self._patch_llm_offline(monkeypatch)
        expl = explain_error("Вопрос", "неверный ответ", ["контекст"], _state())
        assert expl["text"]
        assert isinstance(expl["citation"], dict)


class TestAdjustDifficulty:
    def test_ups_after_3_correct(self):
        state = _state(difficulty="easy")
        for _ in range(3):
            adjust_difficulty(state, True)
        assert state.difficulty == "medium"

    def test_down_after_2_wrong(self):
        state = _state(difficulty="medium")
        adjust_difficulty(state, False)
        assert state.difficulty == "medium"
        adjust_difficulty(state, False)
        assert state.difficulty == "easy"

    def test_no_change_at_boundaries(self):
        state = _state(difficulty="hard")
        for _ in range(5):
            adjust_difficulty(state, True)
        assert state.difficulty == "hard"
        state = _state(difficulty="easy")
        for _ in range(5):
            adjust_difficulty(state, False)
        assert state.difficulty == "easy"


class TestKnowledgeMap:
    def test_exponential_smoothing(self):
        state = _state()
        update_knowledge_map(state, "Атмосфера", 1.0)
        assert state.knowledge_map["Атмосфера"] == pytest.approx(0.7 * 0.5 + 0.3 * 1.0)
        update_knowledge_map(state, "Атмосфера", 0.0)
        expected = 0.7 * (0.7 * 0.5 + 0.3 * 1.0)
        assert state.knowledge_map["Атмосфера"] == pytest.approx(expected)


class TestExplainError:
    def test_returns_text_and_citation(self):
        state = _state()
        fake = lambda m: '{"text": "Ошибка в том, что атмосфера — газ.", "citation": {"paragraph": "§12", "source": "Алексеев"}}'
        result = explain_error("Вопрос", "плохой ответ", ["Атмосфера — оболочка."], state, llm_call=fake)
        assert "газ" in result["text"]
        assert result["citation"]["paragraph"] == "§12"

    def test_fallback_empty(self):
        state = _state()
        result = explain_error("Вопрос", "ответ", ["контекст"], state, llm_call=lambda m: "garbage")
        assert result["text"]
        assert isinstance(result["citation"], dict)


class TestRealTutorIntegration:
    """Интеграционный тест полного цикла (генерация → оценка → объяснение) на RouterAI."""

    @pytest.mark.skipif(
        not (BASE_DIR / ".env").exists() or not Settings().ROUTERAI_API_KEY,
        reason="Нет ROUTERAI_API_KEY",
    )
    def test_full_loop(self):
        state = TutorState(
            learner_type="schoolchild", grade="6", subject="география",
            topic="Атмосфера", has_textbook=False, mode="quiz",
        )
        context = ["Параграф 12: Атмосфера. Атмосфера — воздушная оболочка Земли, состоящая из азота (78%) и кислорода (21%)."]
        card = generate_question("Атмосфера", context, "easy", state)
        assert card.question
        assert card.difficulty == "easy"

        answer = "Атмосфера — воздушная оболочка Земли, состоящая из азота (78%) и кислорода (21%)."
        graded = evaluate_answer(card.question, answer, context, state)
        assert graded.precheck_passed is True
        assert graded.feedback
        assert graded.model_used in ("tutor", "expert", "reference")

        update_knowledge_map(state, card.topic, graded.score)
        assert state.knowledge_map.get(card.topic) is not None

        explanation = explain_error(card.question, "не знаю", context, state)
        assert explanation["text"]


# ----------------------------------------------------------------------
# parse_llm_json — edge cases (Фаза 5 тестов)
# ----------------------------------------------------------------------
class TestParseLlmJson:
    """Тесты улучшенной функции parse_llm_json с балансировкой скобок."""

    @pytest.mark.parametrize(
        ("input_str,expected_keys"),
        [
            # Простой валидный JSON
            ('{"title": "test", "sections": []}', ["title", "sections"]),
            # Markdown обёртка ```json ... ```
            ('```json\n{"title": "markdown_test"}\n```', ["title"]),
            # Текст вокруг JSON
            ("Some intro text {\"title\": \"wrapped\"} trailing text", ["title"]),
            # Вложенные объекты (проверка что скобки внутри строк не мешают)
            ('{"title": "nested", "meta": {"a": 1, "b": 2}}', ["title", "meta"]),
            # Секции с телами
            ('{\n  "sections": [\n    {"body": "hello world"}\n  ]\n}', ["sections"]),
            # Пустой ввод
            ("", []),
            # None-подобный
            (None, []),
            # Нет открывающей скобки
            ("Not valid JSON {{{", []),
            # Не закрытая скобка
            ('{"broken": true', []),
            # JSON с фигурной скобкой внутри строкового значения
            ('{"title": "\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u0441 } \u0432\u043d\u0443\u0442\u0440\u0438", "hook": "\u0432\u043e\u043f\u0440\u043e\u0441?"}', ["title", "hook"]),
            # JSON с экранированными кавычками (валидный JSON)
            ('{"definition": "This is an \\\"explanation\\\" in quotes"}', ["definition"]),
            # JSON с вложенным JSON-подобным значением (типичная ошибка модели — esc escaped)
            ('{"section": "body with {inner}", "ok": 1}', ["section", "ok"]),
            # Array вместо объекта — должно вернуть {}
            ('[1, 2, 3]', []),
        ],
    )
    def test_parse_llm_json_various_inputs(self, input_str, expected_keys):
        result = parse_llm_json(input_str)
        assert isinstance(result, dict)
        for key in expected_keys:
            assert key in result, f"Expected key '{key}' not found in {result}"

    def test_parse_llm_json_with_citations(self):
        """JSON с цитатами типа §12 содержит спецсимволы."""
        data = parse_llm_json('{"title": "Атмосфера", "sections": [{"body": "Определение тела.", "citation": "§12"}]}')
        assert data["title"] == "Атмосфера"
        assert data["sections"][0]["citation"] == "§12"

    def test_parse_llm_json_russian_text(self):
        """JSON с русским текстом должен парситься корректно."""
        data = parse_llm_json('{"title": "Атмосфера Земли", "hook": "Вы знаете, почему небо голубое?"}')
        assert data["title"] == "Атмосфера Земли"
        assert data["hook"] == "Вы знаете, почему небо голубое?"

    def test_parse_llm_json_deeply_nested_braces_in_string(self):
        """Много фигурных скобок внутри строки не должны ломать парсинг."""
        s = '{"a": "xxx{yyy{zzz", "b": "aaa}bbb}ccc", "c": true}'
        data = parse_llm_json(s)
        assert data["a"] == "xxx{yyy{zzz"
        assert data["b"] == "aaa}bbb}ccc"
        assert data["c"] is True
