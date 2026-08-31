import json
from pathlib import Path
from datetime import datetime, timedelta
from src.review import ReviewBank, ReviewCard, card_id_for

def _rec(question="Что такое атмосфера?", correct=False):
    return {"question": question, "options": None, "answer_type": "open",
            "difficulty": "medium", "topic": "Атмосфера", "subject": "география",
            "correct_answer": "Газовая оболочка Земли", "correct": correct,
            "score01": 0.0 if not correct else 1.0}

def test_card_id_stable():
    assert card_id_for("вопрос") == card_id_for("вопрос")
    assert len(card_id_for("вопрос")) == 16

def test_add_from_record_and_dedupe(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    assert bank.add_from_record(_rec()) is True
    assert bank.add_from_record(_rec()) is False  # дубль по question
    cards = bank.to_dicts()
    assert len(cards) == 1

def test_get_due_only_due(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    bank.add_from_record(_rec("q1"))
    bank.add_from_record(_rec("q2"))
    c1 = bank.get(card_id_for("q1"))
    bank.review_card(c1.card_id, correct=True)  # due_at смещён на 1 день → не должен быть due
    due = bank.get_due(limit=5)
    assert len(due) == 1
    assert due[0].question == "q2"

def test_sm2_interval_growth(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    bank.add_from_record(_rec("q"))
    c = bank.get(card_id_for("q"))
    c = bank.review_card(c.card_id, correct=True)
    assert c.interval_days == 1.0
    assert c.reps == 1
    c = bank.review_card(c.card_id, correct=True)
    assert c.interval_days > 1.0
    assert c.reps == 2

def test_sm2_lapse_resets(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    bank.add_from_record(_rec("q"))
    c = bank.get(card_id_for("q"))
    for _ in range(3):
        c = bank.review_card(c.card_id, correct=True)
    assert c.reps == 3
    c = bank.review_card(c.card_id, correct=False)
    assert c.reps == 0
    assert c.interval_days == 1.0
    assert c.lapses == 1
    assert c.ease < 2.5

def test_stats(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    bank.add_from_record(_rec("q1"))
    bank.add_from_record(_rec("q2", correct=False))
    s = bank.stats()
    assert s["total"] == 2
    assert "due" in s and "by_topic" in s

def test_corrupt_bank_fails_soft(tmp_path):
    p = tmp_path / "s1.json"
    p.write_text("not json", encoding="utf-8")
    bank = ReviewBank(tmp_path, "s1")
    assert bank.get_due() == []
