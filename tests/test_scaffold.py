import re
from src.scaffold import hint_for, subtask_step

def test_hint_level1_uses_keywords_from_answer():
    hint = hint_for("Что такое атмосфера?", "Газовая оболочка Земли, состоит из азота и кислорода.",
                    ["Газовая оболочка Земли"], level=1)
    assert "оболочк" in hint.lower() or "газов" in hint.lower()

def test_hint_level2_reveals_beginning():
    hint = hint_for("Что такое атмосфера?", "Газовая оболочка Земли, состоит из азота и кислорода.",
                    ["Газовая оболочка Земли"], level=2)
    assert "Газовая оболочка" in hint

def test_hint_level_clamped():
    h1 = hint_for("q", "a b c d e f", ["ctx"], level=0)
    h3 = hint_for("q", "a b c d e f", ["ctx"], level=99)
    assert isinstance(h1, str) and isinstance(h3, str)

def test_hint_fallback_no_correct_answer():
    hint = hint_for("q", "", ["контекст с термином"], level=1)
    assert hint.strip() != ""

def test_subtask_step():
    assert subtask_step(["шаг1", "шаг2"], 0) == "шаг1"
    assert subtask_step(["шаг1"], 5) == "шаг1"
