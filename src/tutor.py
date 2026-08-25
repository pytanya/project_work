"""
EduTutor — тьюторинг-цикл (раздел 7).

- grade_prompt(grade): параметризация «понятного языка» по классам (Ж-3, 7.1).
- generate_question: RAG-контекст → QuizCard (дешёвая модель — простые вопросы,
  TUTOR_MODEL — сложные; 7.1).
- simplicity_precheck: rule-based пре-оценка ответа (В-2: длина/ключевые термины).
- evaluate_answer: пре-оценка → финальная оценка (TUTOR основной поток, EXPERT —
  сложные/нестандартные ответы, критерий Ж-8).
- adjust_difficulty: ↑ при 3+ правильных подряд, ↓ при 2+ ошибках.
- explain_error: объяснение с цитатой §N.
- anti-repeat: история заданных вопросов (13.2).
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from api.schemas import DiagramEdge, DiagramNode, Lesson, LessonDiagram, LessonSection, QuizCard
from .states import TutorState

logger = logging.getLogger("edututor.tutor")

MAX_EXPLANATION_CHARS = 2500

# Ключевые термины берём из контекста вопроса для пре-оценки (В-2)
# PRE_CHECK_MIN_LENGTH — определён ниже, рядом с simplicity_precheck()


# ----------------------------------------------------------------------
# JSON-парсинг ответа LLM (с fallback)
# ----------------------------------------------------------------------
def _extract_json_block(text: str) -> Optional[str]:
    """Извлекает JSON из fenced code block (```json ... ```)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    return m.group(1).strip() if m else None


def _find_json_bounds(text: str) -> Optional[Tuple[int, int]]:
    """Находит границы JSON ({...}) в тексте с балансировкой скобок.

    Учитывает строковые литералы и экранирование (\\", \\\\), чтобы не
    среагировать на «}» внутри строкового значения.

    Возвращает (start, end) или None.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    end = -1
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i
                break

    return (start, end + 1) if (end != -1 and end > start) else None


def _clean_markdown_json(text: str) -> str:
    """Очищает текст от markdown-обёрток вокруг JSON."""
    text = text.strip()
    # Убираем fenced code block
    inner = _extract_json_block(text)
    if inner:
        return inner
    # Если текст начинается с { — убираем всё до него
    if text.startswith("{"):
        return text
    return text.strip()


def parse_llm_json(text: str) -> Dict[str, Any]:
    """Извлечение JSON из ответа LLM (возможен текст вокруг / ```json ```).

    Использует балансировку скобок с учётом строковых литералов и экранирования,
    чтобы корректно находить конец JSON даже при наличии закрывающих фигурных
    скобок внутри строк (например в escaped HTML или вложенных JSON-подобных значениях).

    Retry-логика: если первый parse не удался, очищаем markdown-обёртки и пробуем заново.
    """
    text = (text or "").strip()
    if not text:
        return {}

    # Попытка 1: прямой парсинг с балансировкой скобок
    bounds = _find_json_bounds(text)
    if bounds:
        candidate = text[bounds[0]:bounds[1]]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Попытка 2: очистить markdown и распарсить заново
    cleaned = _clean_markdown_json(text)
    if cleaned != text:
        bounds2 = _find_json_bounds(cleaned)
        if bounds2:
            candidate2 = cleaned[bounds2[0]:bounds2[1]]
            try:
                data2 = json.loads(candidate2)
                if isinstance(data2, dict):
                    return data2
            except json.JSONDecodeError:
                pass

    # Попытка 3: попробовать json.loads напрямую (на случай если текст — чистый JSON)
    try:
        data3 = json.loads(cleaned)
        if isinstance(data3, dict):
            return data3
    except (json.JSONDecodeError, TypeError):
        pass

    return {}


def _score01(value: Any) -> float:
    """Приводит оценку LLM (0..10 или 0..1) к 0..1."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    if v > 1.0:
        return max(0.0, min(1.0, v / 10.0))
    return max(0.0, min(1.0, v))


def generate_text(
    messages: List[Dict[str, str]],
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
    role: str = "tutor",
    temperature: float = 0.3,
    max_tokens: Optional[int] = 512,
) -> str:
    """Вызов LLM: реальный стриминг токенов (on_token) или обычный вызов.

    - llm_call задан (мок в тестах / инъекция) → текст берём из него, on_token
      лишь ретранслирует результат в UI (реальное стриминговое качество только
      в продакшене, где llm_call=None);
    - llm_call не задан + on_token → LLMClient.chat_stream(stream=True), токены
      уходят в браузер;
    - иначе → обычный LLMClient.chat.
    """
    if llm_call is not None:
        text = llm_call(messages)
        if on_token is not None:
            on_token(text)
        return text
    from .llm_client import LLMClient

    client = LLMClient(role=role)
    try:
        if on_token is not None:
            resp = client.chat_stream(messages, on_chunk=on_token,
                                      temperature=temperature, max_tokens=max_tokens)
            return resp.content or ""
        return client.chat(messages, temperature=temperature, max_tokens=max_tokens).content or ""
    except Exception as exc:
        # Офлайн / недоступен LLM: возвращаем пустую строку — каждый вызывающий
        # (lesson/explain/deep_dive) имеет template-fallback из RAG-контекста.
        logger.warning("generate_text: LLM недоступен (%s) — template-fallback", exc)
        return ""


# ----------------------------------------------------------------------
# grade_prompt (Ж-3)
# ----------------------------------------------------------------------
def grade_prompt(grade: Optional[str]) -> str:
    """Фрагмент system-промпта по классу (таблица 7.1)."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        g = 0
    if g and g <= 6:
        return (
            "Обучаемый — ученик 5-6 класса. Используй простые слова, короткие "
            "предложения, без абстрактных терминов. Вопросы — на один факт/шаг."
        )
    if g and g <= 9:
        return (
            "Обучаемый — ученик 7-9 класса. Допустимы термины из учебника, "
            "вопросы на понимание (2 шага)."
        )
    if g and g <= 11:
        return (
            "Обучаемый — ученик 10-11 класса. Допустимы анализ и синтез, "
            "вопросы multiple-correct и причинно-следственные."
        )
    return "Обучаемый — студент. Уровень — высшее образование, допустима терминология."


def difficulty_for_grade(grade: Optional[str]) -> str:
    """Стартовая сложность по классу (easy 5-6, medium 7-9, hard 10-11)."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return "medium"
    if g and g <= 6:
        return "easy"
    if g and g <= 9:
        return "medium"
    return "hard"


def _diagram_grade_hint(grade: Optional[str]) -> str:
    """Ограничение сложности схемы по классу (dual-coding без противоречий с уровнем)."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        g = 0
    if g and g <= 6:
        return "Схема для ученика 5-6 класса: 2-3 узла, очень простые подписи."
    if g and g <= 9:
        return "Схема для ученика 7-9 класса: 3-4 узла, короткие подписи-термины."
    if g and g <= 11:
        return "Схема для ученика 10-11 класса: 4-6 узлов, допустимы аналитические связи."
    return "Схема для студента: 4-6 узлов, допустимы аналитические связи."


# ----------------------------------------------------------------------
# Генерация вопроса
# ----------------------------------------------------------------------
def _question_prompt(
    topic: str,
    context: List[str],
    difficulty: str,
    grade: Optional[str],
    curriculum: Optional[str],
    simple: bool,
    asked: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor, генерируешь вопрос учебного квиза. "
        + grade_prompt(grade)
        + (
            " Сгенерируй ПРОСТОЙ фактологический вопрос по контексту."
            if simple
            else " Сгенерируй вопрос на понимание/применение."
        )
        + (
            f" Сложность: {difficulty}. Учебная программа: {curriculum}."
            if curriculum
            else f" Сложность: {difficulty}."
        )
        + (
            " Верни строго JSON: {\"question\": \"...\", \"options\": [\"...\"] или null, "
            "\"answer_type\": \"single\"|\"multiple\"|\"open\", \"topic\": \"<тема>\", "
            "\"correct_answers\": [\"правильный вариант/модельный ответ\"], "
            "\"excerpt\": \"<короткий отрывок текста из контекста, на который ссылается вопрос, до 3 строк>\"}. "
            "Для open-вопроса options=null, correct_answers = [\"эталонный ответ\"]. "
            "Для single — ровно 1 правильный вариант, для multiple — все правильные. "
            "ВАЖНО: варианты-дистракторы делай правдоподобными — они должны быть похожи "
            "на правильный по теме/форме, но неверны по смыслу (никакой очевидной абсурдности, "
            "одинаковой длины и стиля с правильным). "
            "Поле excerpt ОБЯЗАТЕЛЬНО — это цитата из контекста (до 3 строк), на которую ссылается вопрос."
        )
    )
    if asked:
        system += (
            " Уже задавали такие вопросы: "
            + "; ".join(str(q) for q in asked[-10:])
            + ". НЕ повторяй их по смыслу — задай другой вопрос по тому же материалу."
        )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст:\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_question(
    topic: str,
    context: List[str],
    difficulty: str,
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
    question_id: Optional[str] = None,
) -> QuizCard:
    """Генерация вопроса по RAG-контексту (дешёвая/тьютор-модель решается вызывающим).

    llm_call — инъекция (мок в тестах); on_token — стриминг токенов в UI. При
    llm_call=None в продакшене используется chat_stream (реальный стриминг).
    """
    simple = difficulty == "easy"
    messages = _question_prompt(
        topic, context, difficulty, state.grade, state.curriculum, simple=simple,
        asked=list(state.asked_questions),
    )
    if llm_call is not None:
        raw = llm_call(messages)
        if on_token is not None:
            on_token(raw)
    else:
        from .llm_client import LLMClient

        client = LLMClient(role="tutor")
        try:
            if on_token is not None:
                resp = client.chat_stream(messages, on_chunk=on_token, temperature=0.3, max_tokens=512)
                raw = resp.content or ""
            else:
                raw = client.chat(messages, temperature=0.3, max_tokens=512).content or ""
        except Exception as exc:
            # Офлайн: шаблонный вопрос из контекста (fallback ниже в этой функции).
            logger.warning("generate_question: LLM недоступен (%s) — шаблонный вопрос", exc)
            raw = ""
    data = parse_llm_json(raw)
    if not data or not data.get("question"):
        # Fallback: шаблонный вопрос из контекста
        snippet = (context[0] if context else topic)[:120]
        data = {
            "question": f"Что говорится в материале о «{topic}»?",
            "options": None,
            "answer_type": "open",
            "topic": topic,
            "excerpt": (context[0] if context else topic)[:200],
        }

    # Защита от вырожденных/пустых вариантов ответа:
    # Если options был указан, но пустой массив или массив строк с пустыми строками — убираем
    raw_options = data.get("options")
    cleaned_options = None
    if isinstance(raw_options, list) and len(raw_options) > 0:
        cleaned_options = [str(o).strip() for o in raw_options if str(o).strip()]
        if len(cleaned_options) < 2 and difficulty != "easy":
            # Для single/open с < 2 вариантами — генерируем из контекста
            cleaned_options = None
    if not cleaned_options and raw_options is not None and difficulty == "easy":
        # Генерируем простые дефолтные варианты для easy из контекста
        cleaned_options = ["Да", "Нет", "Затрудняюсь ответить"]

    qid = question_id or f"q{len(state.asked_questions) + 1}"
    excerpt_raw = data.get("excerpt")
    excerpt = str(excerpt_raw).strip() if excerpt_raw else ""
    card = QuizCard(
        question_id=qid,
        question=str(data.get("question", "")).strip(),
        options=cleaned_options,
        answer_type=data.get("answer_type") if data.get("answer_type") in ("single", "multiple", "open") else "open",
        difficulty=difficulty,
        topic=str(data.get("topic") or topic),
        excerpt=excerpt,
    )
    # Эталонные ответы генерирует LLM (мозг); они НЕ входят в QuizCard/UI.
    refs = data.get("correct_answers")
    state.current_answers = [str(r).strip() for r in refs if str(r).strip()] if isinstance(refs, list) else []
    state.asked_questions.append(card.question)  # тексты вопросов — для антидубликата (7.3.2)
    state.current_question = card
    return card


def is_duplicate_question(
    embedder: Any,
    new_question: str,
    prev_questions: List[str],
    threshold: float = 0.85,
) -> bool:
    """Семантический антидубликат (спека 7.3.2): cosine-близость нового вопроса
    к любому из уже заданных ≥ threshold → дубль (True).

    При недоступности эмбеддера или вырожденных векторах возвращает False
    (не блокируем генерацию вопроса).
    """
    if not prev_questions or not (new_question or "").strip():
        return False
    try:
        new_vec = embedder.encode_query(new_question)
        prev_vecs = embedder.encode(list(prev_questions))
    except Exception:
        return False

    def _cos(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    return any(_cos(new_vec, pv) >= threshold for pv in prev_vecs)


# ----------------------------------------------------------------------
# Урок: структурированное объяснение темы перед квизом (режим lesson)
# ----------------------------------------------------------------------
def _lesson_prompt(topic: str, context: List[str], grade: Optional[str], curriculum: Optional[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor. Составь структурированный УРОК по теме ученика "
        "ТОЛЬКО на основе контекста учебника. "
        + grade_prompt(grade)
        + (
            f" Учебная программа: {curriculum}." if curriculum else ""
        )
        + (
            "\n\nОБЯЗАТЕЛЬНАЯ СТРУКТУРА ОТВЕТА — строго JSON-объект:\n"
            "{\n"
            '  "title": "Заголовок урока",\n'
            '  "hook": "Интересный вопрос-зацепка?",\n'
            '  "definition": "Краткое определение темы в 1-2 простых предложениях.",\n'
            '  "key_terms": [\n'
            '    {"term": "термин1", "definition": "краткое определение"},\n'
            '    {"term": "термин2", "definition": "краткое определение"}\n'
            "  ],\n"
            '  "sections": [\n'
            '    {\n'
            '      "heading": "Подтема 1",\n'
            '      "body": "Объяснение 2-4 предложения простыми словами.",\n'
            '      "citation": "§5",\n'
            '      "check_question": "Вопрос на понимание после этой секции?"\n'
            "    }\n"
            "  ],\n"
            '  "summary": "Итог в 1-2 предложениях."\n'
            "}\n\n"
            "--- ПРАВИЛА ---\n"
            "- Каждая секция ОБЯЗАТЕЛЬНО должна иметь непустое поле \"body\"\n"
            "- Минимум 1 секция, максимум 3 (не больше!)\n"
            "- Каждое поле — обычная строка, НЕ вкладывай JSON-строки внутрь\n"
            "- Предложения короткие, простые, без канцелярита\n"
            "- НЕ выдумывай факты за пределами контекста\n"
            "- Если в контексте нет информации для какого-то поля — оставь его пустым \"\"\n"
            "- Поле \"citation\" — номер параграфа/страницы из контекста, если виден (§N), иначе \"\"\n"
            "- КОНТЕКСТ МОЖЕТ СОДЕРЖАТЬ МУСОР СЛАЙД-ШОУ: строки вида «Часть N», «Слайд N», "
            "«Вернуться в меню», «Презентация онлайн», «Категория: …», размеры файлов («565.99K»), "
            "имена докладчиков. Игнорируй такой мусор и НЕ включай его в урок.\n"
            "- Если в контексте нет ни одного связного предложения — верни JSON с пустыми "
            "полями (\"definition\": \"\", \"sections\": []), не пересказывай фрагменты.\n"
            "- НЕ копируй контекст дословно: секции — это твой пересказ, а не цитата фрагмента.\n"
        )
        + _diagram_grade_hint(grade)
        + (
            "\n\nДИАГРАММА (обязательно добавь):\n"
            "Добавь поле \"diagram\": {\n"
            '  "kind": "flow",\n'
            '  "title": "Название схемы",\n'
            '  "nodes": [{"id": "n1", "label": "Термин 1-3 слова"}],\n'
            '  "edges": [{"source": "n1", "target": "n2", "label": "связь"}]\n'
            "}\n"
            "kind: 'flow' (этапы) | 'cycle' (цикл) | 'map' (координаты 0..1).\n"
            "2-5 узлов, не больше 6 связей. Диаграмма отражает ТОЛЬКО то, что в секциях."
        )
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст учебника:\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _diagram_from_data(raw: Any) -> Optional[LessonDiagram]:
    """Строит LessonDiagram из JSON-ответа LLM с санитизацией.

    Отбрасываются: неизвестные kind, узлы без id/label, связи к несуществующим узлам;
    координаты карты клампируются в 0..1; размеры ограничены (5 узлов / 6 связей).
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind") if raw.get("kind") in ("flow", "cycle", "map") else "flow"
    nodes: List[DiagramNode] = []
    raw_nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
    for n in raw_nodes[:5]:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip()
        label = str(n.get("label") or "").strip()
        if not nid or not label:
            continue
        node = DiagramNode(id=nid[:40], label=label[:80])
        if kind == "map":
            try:
                node.x = max(0.0, min(1.0, float(n.get("x"))))
                node.y = max(0.0, min(1.0, float(n.get("y"))))
            except (TypeError, ValueError):
                node.x, node.y = None, None
        nodes.append(node)
    if not nodes:
        return None
    ids = {n.id for n in nodes}
    edges: List[DiagramEdge] = []
    raw_edges = raw.get("edges") if isinstance(raw.get("edges"), list) else []
    for e in raw_edges[:6]:
        if not isinstance(e, dict):
            continue
        src = str(e.get("source") or "").strip()
        tgt = str(e.get("target") or "").strip()
        if src not in ids or tgt not in ids:
            continue
        color = e.get("color") if e.get("color") in ("warm", "cold") else "neutral"
        edges.append(DiagramEdge(
            source=src, target=tgt,
            label=str(e.get("label") or "").strip()[:40],
            color=color,
        ))
    return LessonDiagram(
        kind=kind,
        title=str(raw.get("title") or "").strip()[:80],
        nodes=nodes,
        edges=edges,
    )


def _clean_plain_field(value: Any) -> str:
    """Поле урока должно быть обычным текстом.

    Модель иногда «вкладывает» весь JSON-объект урока строкой в первое поле
    (например, `definition` = "{...все поля...}"). Такие значения очищаем, чтобы
    в UI не показывался сырой JSON вместо карточки урока.
    """
    if isinstance(value, dict) or isinstance(value, list):
        return ""
    text = str(value or "").strip().lstrip("\ufeff")
    if not text:
        return ""
    if text.startswith("{") or text.startswith("["):
        return ""
    # JSON с обёрткой в кавычки: '\"{...}\"', "'{...}'"
    inner = text.strip("\"'")
    if inner.startswith("{") or inner.startswith("["):
        return ""
    return text


def _lesson_from_data(data: Dict[str, Any], topic: str) -> Lesson:
    """Строит структурированный Lesson из JSON-ответа LLM (нормализация типов).

    Мусорные/пустые поля отбрасываются — урок никогда не содержит пустых карточек
    и не выводит сырой JSON (вложенный объект в поле → очищается, _clean_plain_field).
    """
    sections: List[LessonSection] = []
    raw_sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    for s in raw_sections[:4]:
        if not isinstance(s, dict):
            continue
        body = _clean_plain_field(s.get("body"))
        if not body:
            continue
        sections.append(LessonSection(
            heading=_clean_plain_field(s.get("heading")),
            body=body,
            citation=_clean_plain_field(s.get("citation")),
            source=_clean_plain_field(s.get("source")),
            check_question=_clean_plain_field(s.get("check_question")),
        ))
    key_terms = []
    raw_terms = data.get("key_terms") if isinstance(data.get("key_terms"), list) else []
    for t in raw_terms[:5]:
        if isinstance(t, dict):
            term = _clean_plain_field(t.get("term"))
            tdef = _clean_plain_field(t.get("definition"))
            if term and tdef:
                key_terms.append({"term": term, "definition": tdef})
    title = _clean_plain_field(data.get("title")) or topic
    hook = _clean_plain_field(data.get("hook"))
    definition = _clean_plain_field(data.get("definition"))
    summary = _clean_plain_field(data.get("summary"))
    
    # Fallback: если LLM вернул hook/definition но не создал секции —
    # используем определение как первую секцию, чтобы контент не терялся
    if not sections and definition:
        sections = [LessonSection(body=definition)]
    
    return Lesson(
        title=title,
        hook=hook,
        definition=definition,
        key_terms=key_terms,
        diagram=_diagram_from_data(data.get("diagram")),
        sections=sections,
        summary=summary,
    )


MAX_REPAIR_SECTIONS = 6


def _repair_lesson_from_text(text: str, topic: str) -> Lesson:
    """Собирает Lesson из сплошного текста (LLM проигнорировал JSON).

    Параграфы (по переводам строк) становятся секциями; первый абзац — определение,
    последний — итог (при 4+ абзацах). Консервативно: не выдумываем заголовки.
    JSON-абзацы (вложенный объект) отбрасываются — сырой JSON никогда не попадает
    в карточки. Секции ограничены (MAX_REPAIR_SECTIONS): «выплюнутый» контекст не
    превращается в бесконечный список фрагментов.
    """
    paragraphs = [_clean_plain_field(p) for p in re.split(r"\n+", (text or "").strip()) if p.strip()]
    paragraphs = [p for p in paragraphs if p]
    if len(paragraphs) >= 4:
        return Lesson(
            title=topic,
            definition=paragraphs[0],
            sections=[LessonSection(body=p) for p in paragraphs[1:-1]][:MAX_REPAIR_SECTIONS],
            summary=paragraphs[-1],
        )
    if len(paragraphs) >= 2:
        return Lesson(
            title=topic,
            definition=paragraphs[0],
            sections=[LessonSection(body=p) for p in paragraphs[1:]][:MAX_REPAIR_SECTIONS],
        )
    return Lesson(title=topic, sections=[LessonSection(body=_clean_plain_field(text))])


def lesson_quality_ok(lesson: Lesson) -> Tuple[bool, str]:
    """Синхронный гейт качества урока перед показом (защита от «выплюнутого» контекста).

    Урок принимается, если есть:
      - структурное обогащение (заголовки секций / «проверь себя» / цитаты /
        ключевые термины / хук) — признак осмысленного урока, либо
      - связная проза (определение ≥50 символов или секция ≥100 символов).

    Голые фрагменты-заголовки («Тест „Творцы Серебряного века“», «Литературная
    гостиная …») и служебный хром слайд-шоу не проходят — такой урок невозможно
    «открыть» как описание, его не показываем.
    """
    from .knowledge import _is_slide_chrome, _is_slideshow_text, _is_web_noise

    def _clean_prose(t: Optional[str]) -> Optional[str]:
        text = (t or "").strip()
        if not text:
            return None
        if _is_slideshow_text(text):
            return None
        lines = [ln for ln in text.splitlines() if ln.strip() and not _is_web_noise(ln)]
        return "\n".join(lines) if lines else None

    definition = _clean_prose(lesson.definition) or ""
    section_pairs = [(s, _clean_prose(s.body)) for s in (lesson.sections or [])]
    section_pairs = [(s, b) for s, b in section_pairs if b]
    sections = [b for _, b in section_pairs]
    if not definition and not sections:
        return False, "no_content"
    # Служебный хром слайдов в определении/заголовке — источник-презентация.
    # Проверяем «сырые» поля: _clean_prose может вычистить мусорную строку в пустоту.
    if _is_slide_chrome(lesson.definition) or _is_slide_chrome(lesson.title):
        return False, "slideshow_chrome"
    # Структурное обогащение — урок составлен, а не скопирован из контекста
    structural = (
        any((s.heading or "").strip() for s, _ in section_pairs)
        or any((s.check_question or "").strip() for s, _ in section_pairs)
        or any((s.citation or "").strip() for s, _ in section_pairs)
        or bool(lesson.key_terms)
        or bool(_clean_prose(lesson.hook))
    )
    # Связная проза — реальное объяснение, а не заголовок
    prose = bool(definition and len(definition) >= 50) or any(len(b) >= 100 for b in sections)
    if structural or prose:
        return True, "ok"
    if sections:
        return False, "fragments"
    return False, "definition_only_shallow"


def generate_lesson(
    topic: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> Lesson:
    """Синтез структурированного урока по RAG-контексту (тьютор-модель).

    Возвращает Lesson (карточки/секции/термины). Если модель не вернула валидную
    структуру — repair: сплошной текст разбивается на секции (не теряется контент).
    on_token — стриминг токенов в браузер.
    """
    messages = _lesson_prompt(topic, context, state.grade, state.curriculum)
    raw = generate_text(messages, llm_call=llm_call, on_token=on_token,
                        role="tutor", temperature=0.4, max_tokens=1200)
    logger.info("generate_lesson[%s]: raw_len=%d starts_with=%r", topic, len(raw), raw[:30] if raw else "")
    data = parse_llm_json(raw)
    logger.info("generate_lesson[%s]: parsed_keys=%r sections_count=%d has_def=%r has_hook=%r",
                topic, list(data.keys()), len(data.get("sections", [])) if isinstance(data.get("sections"), list) else 0,
                bool(data.get("definition")), bool(data.get("hook")))
    lesson = _lesson_from_data(data, topic)
    logger.info("generate_lesson[%s]: lesson_title=%r sections=%d hook=%r def=%r",
                topic, lesson.title, len(lesson.sections), bool(lesson.hook), bool(lesson.definition))
    # Repair: модель вернула не-JSON / JSON без структуры — собираем из сплошного текста.
    if not lesson.sections and not lesson.definition and not lesson.hook:
        text = str(data.get("text") or "").strip()
        if not text and not data:
            # LLM вернул сплошной текст (не JSON) — используем его напрямую
            text = (raw or "").strip()
        
        # Если текст начинается с { или [ — это JSON, который parse_llm_json не распарсил.
        # Попробуем очистить от markdown-обёрток и распарсить заново.
        if text.startswith("{") or text.startswith("["):
            cleaned = re.sub(r'^```(?:json)?\s*', '', text).rstrip('`').strip()
            try:
                retry_data = json.loads(cleaned)
                if isinstance(retry_data, dict):
                    retry_lesson = _lesson_from_data(retry_data, topic)
                    if retry_lesson.sections or retry_lesson.definition or retry_lesson.hook:
                        logger.info("generate_lesson[%s]: retry-parse succeeded after markdown cleanup", topic)
                        return retry_lesson
            except json.JSONDecodeError:
                pass
            # Не удалось распарлить — очищаем, чтобы не показывать сырой JSON в UI
            text = ""
        
        if len(text) < 40:
            text = (context[0] if context else f"Материалы по теме «{topic}» ещё пополняются.")[:1200]
        lesson = _repair_lesson_from_text(text, topic)
    return lesson


# ----------------------------------------------------------------------
# Объяснение темы (режим explain) и глубокий разбор (режим deep_dive)
# ----------------------------------------------------------------------
def _topic_explain_prompt(topic: str, context: List[str], grade: Optional[str], curriculum: Optional[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor. Объясни тему ученику понятным языком по контексту учебника. "
        + grade_prompt(grade)
        + (
            f" Учебная программа: {curriculum}." if curriculum else ""
        )
        + (
            " Структура: что это такое (определение), почему это важно, главные факты, "
            "наглядный пример, итог одним предложением. Пиши связно, без списков-канцелярита "
            "и заголовков-эмодзи. Не выдумывай факты за пределами контекста. "
            "Отвечай ЧИСТЫМ ТЕКСТОМ объяснения — без JSON и обёрток."
        )
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст учебника:\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_explanation(
    topic: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> str:
    """Объяснение темы (режим explain): определение, факты, пример, цитата."""
    messages = _topic_explain_prompt(topic, context, state.grade, state.curriculum)
    raw = generate_text(messages, llm_call=llm_call, on_token=on_token,
                        role="tutor", temperature=0.3, max_tokens=700)
    data = parse_llm_json(raw)
    text = str(data.get("text") or raw or "").strip()
    if text.startswith("{") or text.startswith("["):
        text = ""  # модель вернула JSON вместо чистого текста — не показываем сырой JSON
    if len(text) < 40:
        text = (context[0] if context else f"Материалы по теме «{topic}» ещё пополняются.")[:1200]
    return text


def _deep_dive_prompt(topic: str, context: List[str], grade: Optional[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — эксперт EduTutor, делаешь ГЛУБОКИЙ РАЗБОР темы по нескольким фрагментам учебника. "
        + grade_prompt(grade)
        + (
            " Структура: ключевые понятия и их определения, внутренние связи между разделами, "
            "причинно-следственные цепочки, примеры, типичные ошибки, вывод. Опирайся ТОЛЬКО "
            "на предоставленный контекст, не выдумывай. Указывай параграфы-источники (§N) для "
            "ключевых утверждений. "
            "Отвечай ЧИСТЫМ ТЕКСТОМ разбора — без JSON и обёрток."
        )
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст (несколько разделов):\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_deep_dive(
    topic: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> str:
    """Глубокий разбор (режим deep_dive): multi-chunk синтез эксперт-моделью."""
    messages = _deep_dive_prompt(topic, context, state.grade)
    raw = generate_text(messages, llm_call=llm_call, on_token=on_token,
                        role="expert", temperature=0.2, max_tokens=900)
    data = parse_llm_json(raw)
    text = str(data.get("text") or raw or "").strip()
    if text.startswith("{") or text.startswith("["):
        text = ""  # модель вернула JSON вместо чистого текста — не показываем сырой JSON
    if len(text) < 40:
        text = (context[0] if context else f"Материалы по теме «{topic}» ещё пополняются.")[:1500]
    return text


# ----------------------------------------------------------------------
# Оценка ответа (В-2, Ж-8)
# ----------------------------------------------------------------------
PRE_CHECK_MIN_LENGTH = 15
PRE_CHECK_MIN_WORDS = 3


def simplicity_precheck(answer: str, context: List[str]) -> bool:
    """Rule-based judge-lite (В-2): отсекает ТОЛЬКО пустые/слишком короткие ответы.

    Важно: НЕ требуем совпадения ключевых слов с чанком — ученик может ответить
    своими словами (парафраз), и это корректный ответ. Смысл оценивает LLM
    (evaluate_answer). Здесь — лишь «не пусто и не мусор».
    """
    text = (answer or "").strip()
    if len(text) < PRE_CHECK_MIN_LENGTH:
        return False
    words = re.findall(r"[а-яёa-z]{2,}", text.lower())
    if len(words) < PRE_CHECK_MIN_WORDS:
        return False
    return True


def _decide_eval_model(state: TutorState, answer: str) -> str:
    """Критерий Ж-8: эксперт — для сложных/нестандартных ответов, иначе тьютор."""
    text = (answer or "").strip()
    # 2.1 неструктурированный/развёрнутый (длинный свободный текст)
    if len(text) > 600:
        return "expert"
    # 2.3 повторные ошибки по knowledge_map
    topic = state.current_question.topic if state.current_question else ""
    if topic and state.knowledge_map.get(topic, 0.5) < 0.35:
        return "expert"
    return "tutor"


def _eval_prompt(
    question: str,
    answer: str,
    context: List[str],
    correct_answers: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    system = (
        "Ты — строгий экзаменатор EduTutor. Оцени ответ ученика по эталону из контекста. "
        "Верни строго JSON: {\"score\": <0..10>, \"correct\": true|false, "
        "\"feedback\": \"краткое пояснение ошибки\", \"citation_ok\": true|false}."
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    refs = ", ".join(correct_answers) if correct_answers else ""
    user = f"Вопрос: {question}\nОтвет ученика: {answer}\n"
    if refs:
        user += f"Правильный(е) ответ(ы): {refs}\n"
    user += f"Эталон (контекст):\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _ref_match(answer: str, refs: List[str]) -> bool:
    """Сверка ответа ученика с эталонными ответами (нормализация без учёта регистра/пробелов)."""
    def norm(s: str) -> str:
        return " ".join(str(s).lower().split())

    a = norm(answer)
    for r in refs:
        rn = norm(r)
        if not rn:
            continue
        if a == rn:
            return True
        # для длинных эталонов допускаем вхождение (короткий в длинном и наоборот)
        if len(rn) >= 4 and (rn in a or a in rn):
            return True
    return False


def _answer_context_overlap(answer: str, context: List[str]) -> bool:
    """Эвристика офлайн-проверки открытого ответа: ключевые слова ответа в контексте.

    Не требует LLM (fallback, когда AI-сервис недоступен). Не строгий судья —
    лишь грубая проверка «ответ не пустой и по теме».
    """
    words = re.findall(r"[а-яёa-z]{4,}", (answer or "").lower())
    if not words:
        return False
    hay = " ".join(context).lower()
    hits = sum(1 for w in set(words) if w in hay)
    return hits >= 2 or (len(set(words)) == 1 and hits == 1)


@dataclass
class GradedAnswer:
    score: float  # 0..1
    correct: bool
    feedback: str
    citation_ok: bool
    model_used: str
    precheck_passed: bool


def evaluate_answer(
    question: str,
    answer: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> GradedAnswer:
    """Полная оценка ответа: пре-оценка (В-2) → финальная оценка (Ж-8)."""
    from .llm_client import LLMClient

    # Эталонные ответы, сгенерированные LLM при создании вопроса (не из UI)
    refs = list(state.current_answers)
    q = state.current_question
    is_closed = bool(q and q.answer_type in ("single", "multiple") and q.options)

    # Закрытый вопрос: ответ = выбранный вариант, пре-проверка длины не нужна.
    # Сверка с эталоном детерминирована (эталон LLM предгенерён) — LLM не зовём:
    # совпало → верно, не совпало → неверно с показом правильного варианта.
    if is_closed:
        if refs:
            if _ref_match(answer, refs):
                return GradedAnswer(
                    score=1.0, correct=True, feedback="Верно!", citation_ok=True,
                    model_used="reference", precheck_passed=True,
                )
            return GradedAnswer(
                score=0.0, correct=False,
                feedback=f"Неверно. Правильный ответ: {', '.join(refs)}.",
                citation_ok=True, model_used="reference", precheck_passed=True,
            )
    else:
        precheck = simplicity_precheck(answer, context)
        if not precheck:
            return GradedAnswer(
                score=0.0, correct=False,
                feedback="Ответ слишком короткий — уточните, пожалуйста.",
                citation_ok=False, model_used="rule-based", precheck_passed=False,
            )

    role = _decide_eval_model(state, answer)  # Ж-8
    prompt = _eval_prompt(question, answer, context, correct_answers=refs or None)
    if llm_call is None:
        client = LLMClient(role=role)
        try:
            raw = client.chat(prompt, temperature=0.0, max_tokens=300).content or ""
        except Exception as exc:
            # Офлайн: эвристическая проверка по ключевым словам (не роняем квиз).
            logger.warning("evaluate_answer: LLM недоступен (%s) — rule-based fallback", exc)
            overlap = _answer_context_overlap(answer, context)
            if overlap:
                return GradedAnswer(
                    score=0.7, correct=True,
                    feedback="Ответ принят (проверка по ключевым словам — AI недоступен).",
                    citation_ok=False, model_used="rule-based", precheck_passed=True,
                )
            return GradedAnswer(
                score=0.3, correct=False,
                feedback="Не удалось проверить ответ автоматически: нет доступа к AI. "
                         "Повторите позже или ответьте по тексту учебника.",
                citation_ok=False, model_used="rule-based", precheck_passed=True,
            )
    else:
        raw = llm_call(prompt)
    data = parse_llm_json(raw)
    score01 = _score01(data.get("score", 5.0))
    correct = bool(data.get("correct", score01 >= 0.7))
    return GradedAnswer(
        score=score01,
        correct=correct,
        feedback=str(data.get("feedback", "") or ""),
        citation_ok=bool(data.get("citation_ok", False)),
        model_used=role,
        precheck_passed=True,
    )


# ----------------------------------------------------------------------
# Адаптация сложности (7.1)
# ----------------------------------------------------------------------
def adjust_difficulty(state: TutorState, correct: bool) -> str:
    """↑ при 3+ правильных подряд, ↓ при 2+ ошибках подряд (7.1)."""
    order = ["easy", "medium", "hard"]
    cur = state.difficulty if state.difficulty in order else "medium"
    idx = order.index(cur)

    state.answered_count += 1
    if correct:
        state.correct_count += 1
        state.correct_streak += 1
        state.wrong_streak = 0
        if state.correct_streak >= 3 and idx < len(order) - 1:
            state.correct_streak = 0
            state.difficulty = order[idx + 1]
    else:
        state.wrong_streak += 1
        state.correct_streak = 0
        if state.wrong_streak >= 2 and idx > 0:
            state.wrong_streak = 0
            state.difficulty = order[idx - 1]
    return state.difficulty


def update_knowledge_map(state: TutorState, topic: str, score01: float) -> None:
    """Экспоненциальное сглаживание (Ж-6): 0.7*текущее + 0.3*результат."""
    state.update_knowledge(topic, score01)


# ----------------------------------------------------------------------
# Объяснение ошибки (с цитатой §N)
# ----------------------------------------------------------------------
def _explain_prompt(question: str, answer: str, correct_answer: Optional[str], context: List[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor. Объясни ученику его ошибку доступно для его класса. "
        "Обязательно приведи цитату из учебника с номером параграфа (§N) из контекста. "
        "Верни строго JSON: {\"text\": \"объяснение\", \"citation\": {\"paragraph\": \"§12\", \"source\": \"...\"}}."
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Вопрос: {question}\nОтвет ученика: {answer}\nКонтекст (учебник):\n{ctx}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def explain_error(
    question: str,
    answer: str,
    context: List[str],
    state: TutorState,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Объяснение ошибки с цитатой (EXPERT_MODEL — deep dive, раздел 4.2.2)."""
    from .llm_client import LLMClient

    messages = _explain_prompt(question, answer, None, context)

    if llm_call is not None:
        raw = llm_call(messages)
        if on_token is not None:
            on_token(raw)
    else:
        client = LLMClient(role="expert")
        try:
            if on_token is not None:
                resp = client.chat_stream(messages, on_chunk=on_token, temperature=0.2, max_tokens=500)
                raw = resp.content or ""
            else:
                raw = client.chat(messages, temperature=0.2, max_tokens=500).content or ""
        except Exception as exc:
            # Офлайн: шаблонное объяснение (fallback ниже — «Разберём ошибку подробнее»).
            logger.warning("explain_error: LLM недоступен (%s) — шаблон", exc)
            raw = ""
    data = parse_llm_json(raw)
    citation = data.get("citation") if isinstance(data.get("citation"), dict) else {}
    return {
        "text": str(data.get("text", "") or "Разберём ошибку подробнее."),
        "citation": {
            "paragraph": citation.get("paragraph", ""),
            "source": citation.get("source", ""),
        },
    }
