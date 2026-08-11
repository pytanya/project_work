"""Тесты NLP EduTutor: rule-based intent + regex-NER (Слайс 2, В-1/В-9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.nlp import classify_intent, extract_entities, ner_empty_rate

INTENT_DATASET = Path(__file__).resolve().parent.parent / "evals" / "intent_dataset.json"


class TestClassifyIntent:
    def test_quiz(self):
        assert classify_intent("Сделай тест по атмосфере") == "quiz"
        assert classify_intent("Проведи квиз по географии") == "quiz"
        assert classify_intent("Задай мне 5 вопросов") == "quiz"

    def test_explain(self):
        assert classify_intent("Объясни, что такое атмосфера") == "explain"
        assert classify_intent("Почему дует ветер?") == "explain"

    def test_deep_dive(self):
        assert classify_intent("Дай развёрнутый ответ по теме океаны") == "deep_dive"
        assert classify_intent("Разбери тему Атмосфера детально") == "deep_dive"

    def test_homework(self):
        assert classify_intent("Помоги с домашним заданием по физике") == "homework"
        assert classify_intent("Реши задачу номер 5") == "homework"

    def test_morphology_forms(self):
        # Падежные формы: «контрольную»/«викторину»/«проверочной»
        assert classify_intent("Хочу контрольную работу по истории") == "quiz"
        assert classify_intent("Сделай викторину по биологии") == "quiz"
        assert classify_intent("Прогони меня по проверочной") == "quiz"

    def test_unknown_defaults_to_explain(self):
        assert classify_intent("Привет!") == "explain"

    def test_empty_query(self):
        assert classify_intent("") == "explain"

    def test_llm_fallback_used_when_rule_based_empty(self):
        calls = {"n": 0}

        def llm(q):
            calls["n"] += 1
            return "quiz"

        assert classify_intent("непонятная фраза без ключевых слов", llm_classify=llm) == "quiz"
        assert calls["n"] == 1

    def test_llm_fallback_ignored_for_known(self):
        def llm(q):
            return "homework"

        # rule-based уже дал quiz — LLM не вызываем и не перебиваем
        assert classify_intent("Сделай тест", llm_classify=llm) == "quiz"


class TestExtractEntities:
    def test_full_query(self):
        e = extract_entities("6 класс, география, Алексеев, параграф 12")
        assert e.grade == "6"
        assert e.subject == "география"
        assert e.author == "алексеев"
        assert e.chapter == "12"

    def test_section_symbol(self):
        e = extract_entities("11 класс физика Мякишев §34")
        assert e.grade == "11"
        assert e.subject == "физика"
        assert e.author == "мякишев"
        assert e.chapter == "34"

    def test_empty_query(self):
        e = extract_entities("")
        assert e.grade is None and e.subject is None and e.author is None
        assert e.chapter is None

    def test_has_empty(self):
        assert extract_entities("расскажи про вулканы").has_empty() is True
        assert extract_entities("6 класс география алексеев §12").has_empty() is False

    def test_has_missing(self):
        e = extract_entities("6 класс география")
        assert e.has_missing() is True  # нет автора/главы
        assert e.has_empty() is False   # что-то извлечено

    def test_llm_supplement_when_regex_empty(self):
        def llm(q):
            from src.nlp import Entities
            return Entities(subject="биология", topic="клетка")

        e = extract_entities("покажи строение", llm_extract=llm)
        assert e.subject == "биология"
        assert e.topic == "клетка"

    def test_llm_supplement_not_override_regex(self):
        def llm(q):
            from src.nlp import Entities
            return Entities(subject="биология")

        e = extract_entities("6 класс физика", llm_extract=llm)
        assert e.subject == "физика"  # regex-значение сохраняется
        assert e.grade == "6"


class TestNerEmptyRate:
    def test_rate(self):
        queries = [
            "6 класс география Алексеев параграф 12",
            "расскажи про вулканы",
            "11 класс физика",
        ]
        assert ner_empty_rate(queries) == pytest.approx(1 / 3, abs=1e-3)


class TestIntentDataset:
    """В-9: intent accuracy ≥ 0.8 на intent-датасете (golden set)."""

    def test_dataset_exists(self):
        assert INTENT_DATASET.exists()

    def test_intent_accuracy_at_least_0_8(self):
        data = json.loads(INTENT_DATASET.read_text(encoding="utf-8"))
        items = data["items"]
        assert len(items) >= 20
        correct = sum(1 for it in items if classify_intent(it["query"]) == it["intent"])
        accuracy = correct / len(items)
        assert accuracy >= 0.8, f"intent accuracy = {accuracy:.2f} < 0.8"
