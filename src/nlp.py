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


# ----------------------------------------------------------------------
# Запрос страниц сканированного учебника (3.2)
# ----------------------------------------------------------------------
@dataclass
class DocRequest:
    """Результат разбора ответа «страницы + тема» для OCR.

    pages: (start, end) — физические страницы PDF (1-индекс), или None.
    all_pages: True — «все» (полный OCR).
    topic: тема/урок от ученика (для validate_topic_in_text), или None.
    """

    pages: Optional[tuple] = None
    all_pages: bool = False
    topic: Optional[str] = None
    lesson: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.all_pages or self.pages is not None


_PAGE_RANGE_RE = re.compile(r"(\d{1,4})\s*[-–—]\s*(\d{1,4})")
_PAGE_SINGLE_RE = re.compile(r"(\d{1,4})")
_PAGE_LIST_RE = re.compile(r"(\d{1,4})\s*[,;]\s*(\d{1,4})")
_PAGE_BY_RE = re.compile(r"(\d{1,4})\s*(?:по|до|по-)\s*(\d{1,4})", re.IGNORECASE)
_ALL_RE = re.compile(r"\b(все|весь учебник|вся|всё)\b", re.IGNORECASE)
_LESSON_RE = re.compile(r"\b(?:урок|lesson|юнит|unit|модуль|module)\s*(\d{1,3})\b", re.IGNORECASE)
# «мусорные» слова, которые не являются темой
_NOISE = {"стр", "страниц", "страница", "страницы", "с", "по", "и", "то", "это", "учебник", "урок", "все", "напечатай"}


def parse_doc_request(answer: str, num_pages: int) -> DocRequest:
    """Разбор ответа ученика: «12-15, Атмосфера», «стр. 12–15», «с 12 по 15»,
    «12,13,14», «урок 3 про Родину», «все».

    Возвращает DocRequest. pages клампится в 1..num_pages; если диапазон
    некорректен (start>end, за границами) — pages=None.
    """
    text = (answer or "").strip()
    if not text:
        return DocRequest()

    req = DocRequest()

    # «все» — полный OCR
    if _ALL_RE.search(text):
        req.all_pages = True

    # урок N → lesson (страницы неизвестны без оглавления)
    lesson_m = _LESSON_RE.search(text)
    if lesson_m:
        req.lesson = lesson_m.group(1)

    # диапазон "12-15" / "12–15" / "с 12 по 15" / "от 12 до 15"
    m = _PAGE_RANGE_RE.search(text)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        req.pages = _clamp_range(start, end, num_pages)
    else:
        m_by = _PAGE_BY_RE.search(text)
        if m_by:
            req.pages = _clamp_range(int(m_by.group(1)), int(m_by.group(2)), num_pages)
        else:
            # список "12,13,14,15" — берём min..max всех чисел
            if "," in text or ";" in text:
                nums = sorted(int(x) for x in _PAGE_SINGLE_RE.findall(text))
                if nums:
                    req.pages = _clamp_range(nums[0], nums[-1], num_pages)
            else:
                # одно число "12"
                m3 = _PAGE_SINGLE_RE.search(text)
                if m3 and not req.all_pages:
                    n = int(m3.group(1))
                    req.pages = _clamp_range(n, n, num_pages)

    # тема: остаток после диапазона/«все»
    topic = _extract_topic(text)
    if topic and not _is_noise(topic):
        req.topic = topic

    return req


def _clamp_range(start: int, end: int, num_pages: int) -> Optional[tuple]:
    if start <= 0 or start > end:
        return None
    if num_pages and num_pages > 0:
        end = min(end, num_pages)
        start = min(start, num_pages)
        if start > end:
            return None
    return (start, end)


def _extract_topic(text: str) -> Optional[str]:
    """Тема после разделителя (запятая, точка с запятой, «тема/про»)."""
    lowered = text.lower()
    for sep in [",", ";", "—", "–", "-", "("]:
        if sep in text:
            parts = text.split(sep)
            # берём часть, которая не является только числами/номерами
            for part in parts:
                candidate = part.strip(" ()[]{}\"»«")
                if not candidate:
                    continue
                if _PAGE_RANGE_RE.search(candidate) or _PAGE_SINGLE_RE.fullmatch(candidate.strip()):
                    continue
                if _is_noise(candidate):
                    continue
                if len(candidate) >= 2:
                    return candidate
    # «урок N про X» / «по теме X» / «тема X»
    m = re.search(r"(?:по теме|тема|про|на тему)\s+([^\s,;()]{2,}(?:\s+[^\s,;()]{1,40}){0,6})", lowered, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip(".,;")
    # урок N с названием
    if _LESSON_RE.search(text):
        m2 = re.search(r"урок\s*\d+\s+[\"«]?(.+?)[\"»]?$", lowered)
        if m2 and m2.group(1).strip():
            return m2.group(1).strip()
    return None


def _is_noise(word: str) -> bool:
    w = word.strip(".,;:()[]{}«»\"' —–-").lower()
    return w in _NOISE or not any(ch.isalpha() for ch in w) or w in ("стр", "с", "по", "и", "то")
