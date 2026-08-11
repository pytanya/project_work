"""
EduTutor — NLP: rule-based intent + regex-NER + LLM-дополнение (В-1).

- classify_intent(query): интенты quiz / explain / deep_dive / homework (В-9).
  Rule-based по ключевым словам; при пустом результате — необязательный LLM-классификатор
  (few-shot), затем default (explain).
- extract_entities(query): NER — класс/предмет/автор/глава (тема). Регекс-парсер;
  при пустом результате — необязательное LLM-дополнение.
- has_empty_ner / ner_empty_rate — метрика «доля запросов с пустым NER» (В-1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

INTENTS = ("quiz", "explain", "deep_dive", "homework")

# Порядок приоритета при равном числе совпадений
_INTENT_PRIORITY = {name: i for i, name in enumerate(("quiz", "deep_dive", "explain", "homework"))}

INTENT_KEYWORDS: dict[str, List[str]] = {
    "quiz": [
        "тест", "квиз", "викторин", "вопрос", "контрольн", "проверь", "экзамен",
        "задай", "потренируй", "тренажёр", "тренажер", "проверочн", "зачёт", "зачет",
        "опрос", "прогони", "quiz", "test",
    ],
    "explain": [
        "объясни", "объясните", "расскажи", "расскажите", "поясни", "разъясни",
        "почему", "зачем", "как устроен", "как работает", "как образу", "что такое",
        "разбери", "в чём разница", "чем отлич", "для чего", "из чего состоит",
        "как происходит", "каким образом", "от чего зависит", "по какому принципу",
    ],
    "deep_dive": [
        "глубок", "подробн", "детальн", "углублённ", "углубленн", "deep dive",
        "развёрнут", "развернут", "исчерпывающ", "по нескольким главам",
        "по нескольким параграфам", "синтез",
    ],
    "homework": [
        "домашн", "домашка", "дз", "реши задачу", "реши пример", "реши уравнение",
        "реши задание", "выполни задание", "задание из учебника", "конспект",
        "доклад", "эссе", "сочинен", "реферат", "упражн",
    ],
}

# --- Списки для regex-NER (В-1) ---

SUBJECTS: List[str] = [
    "математика", "алгебра", "геометрия", "физика", "химия", "биология",
    "география", "история", "русский язык", "русский", "литература",
    "обществознание", "информатика", "английский язык", "английский",
    "иностранный язык", "окружающий мир", "природоведение", "астрономия",
    "экономика", "обж", "технология", "музыка", "изо", "физкультура",
    "икт", "правоведение", "экология",
]

AUTHORS: List[str] = [
    "алексеев", "плешаков", "виленкин", "мерзляк", "атанасян", "погорелов",
    "перышкин", "габриелян", "мякишев", "касьянов", "пасечник", "сивоглазов",
    "дронов", "баринова", "агибалова", "донской", "вигасин", "годер",
    "свенцицкая", "макарычев", "никольский", "козлов", "рутенин", "иванова",
    "ладыженская", "баранов", "тростенцова", "канакина", "горецкий", "рамзаева",
    "бененсон", "паутова", "босова", "поляков", "кодько", "лукашик",
]

_GRADE_PATTERNS = [
    r"(\d{1,2})\s*(?:класс|классе|класса|классу)",
    r"(\d{1,2})\s*-\s*?й\s*класс",
]

_CHAPTER_PATTERNS = [
    r"параграф\s*(\d{1,3})",
    r"параграфы\s*(\d{1,3})",
    r"§\s*(\d{1,3})",
    r"глава\s*(\d{1,3})",
    r"раздел\s*(\d{1,3})",
]

_TOPIC_PATTERNS = [
    r"тема\s+[\"«]?([а-яёА-ЯЁ\w \-]{2,60})[\"»]?",
    r"тему\s+[\"«]?([а-яёА-ЯЁ\w \-]{2,60})[\"»]?",
]


@dataclass
class Entities:
    """Сущности запроса: класс/предмет/автор/глава/тема."""

    grade: Optional[str] = None
    subject: Optional[str] = None
    author: Optional[str] = None
    chapter: Optional[str] = None
    topic: Optional[str] = None
    raw: str = ""

    def has_empty(self) -> bool:
        """«Доля запросов с пустым NER» (В-1): не извлечено ни одной сущности."""
        return all(v is None for v in (self.grade, self.subject, self.author, self.chapter, self.topic))

    def has_missing(self) -> bool:
        """Хотя бы одно ключевое поле не извлечено."""
        return any(v is None for v in (self.grade, self.subject, self.author, self.chapter, self.topic))


# ----------------------------------------------------------------------
# Intent
# ----------------------------------------------------------------------
def classify_intent(query: str, llm_classify: Optional[Callable[[str], str]] = None) -> str:
    """Классификация интента запроса (quiz/explain/deep_dive/homework).

    Правила: подсчёт совпадений ключевых слов по интентам, при равном числе —
    приоритет quiz > explain > deep_dive > homework. Если rule-based пусто —
    пробуем LLM-классификатор (few-shot), затем default "explain".
    """
    text = (query or "").lower()
    hits: dict[str, int] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text)
        if count:
            hits[intent] = count

    if hits:
        best = max(hits, key=lambda i: (hits[i], -_INTENT_PRIORITY[i]))
        return best

    if llm_classify is not None:
        try:
            result = (llm_classify(query) or "").strip().lower()
        except Exception:
            result = ""
        if result in INTENTS:
            return result

    return "explain"


# ----------------------------------------------------------------------
# NER
# ----------------------------------------------------------------------
def extract_entities(query: str, llm_extract: Optional[Callable[[str], Entities]] = None) -> Entities:
    """Извлечение сущностей: класс/предмет/автор/глава/тема (regex → LLM-дополнение)."""
    text = (query or "").strip().lower()
    ent = Entities(raw=query or "")

    for pattern in _GRADE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            ent.grade = m.group(1)
            break

    for pattern in _CHAPTER_PATTERNS:
        m = re.search(pattern, text)
        if m:
            ent.chapter = m.group(1)
            break

    for pattern in _TOPIC_PATTERNS:
        m = re.search(pattern, text)
        if m:
            ent.topic = m.group(1).strip()
            break

    subject_hit = _find_first(SUBJECTS, text)
    if subject_hit:
        ent.subject = subject_hit

    author_hit = _find_first(AUTHORS, text)
    if author_hit:
        ent.author = author_hit

    # LLM-дополнение только если регекс-парсер не извлёк ни одной сущности (В-1)
    if ent.has_empty() and llm_extract is not None:
        try:
            extra = llm_extract(query)
        except Exception:
            extra = None
        if extra is not None:
            ent = _merge_entities(ent, extra)

    return ent


def _find_first(candidates: List[str], text: str) -> Optional[str]:
    for cand in candidates:
        if cand in text:
            return cand
    return None


def _merge_entities(base: Entities, extra: Entities) -> Entities:
    return Entities(
        grade=base.grade or extra.grade,
        subject=base.subject or extra.subject,
        author=base.author or extra.author,
        chapter=base.chapter or extra.chapter,
        topic=base.topic or extra.topic,
        raw=base.raw,
    )


# ----------------------------------------------------------------------
# Метрика «доля запросов с пустым NER»
# ----------------------------------------------------------------------
def ner_empty_rate(queries: List[str]) -> float:
    """Доля запросов, где extract_entities вернул пустой NER (В-1)."""
    if not queries:
        return 0.0
    empty = sum(1 for q in queries if extract_entities(q).has_empty())
    return round(empty / len(queries), 4)
