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
from typing import Any, Callable, Dict, List, Optional

from api.schemas import DiagramEdge, DiagramNode, Lesson, LessonDiagram, LessonSection, QuizCard
from .states import TutorState

logger = logging.getLogger("edututor.tutor")

MAX_EXPLANATION_CHARS = 2500

# Ключевые термины берём из контекста вопроса для пре-оценки (В-2)
# PRE_CHECK_MIN_LENGTH — определён ниже, рядом с simplicity_precheck()


# ----------------------------------------------------------------------
# JSON-парсинг ответа LLM (с fallback)
# ----------------------------------------------------------------------
def parse_llm_json(text: str) -> Dict[str, Any]:
    """Извлечение JSON из ответа LLM (возможен текст вокруг / ```json ```)."""
    text = (text or "").strip()
    if not text:
        return {}
    # Убираем fenced code block
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1).strip()
    # Ищем первую { ... } (или [ ... ])
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
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
            "\"correct_answers\": [\"правильный вариант/модельный ответ\"]}. "
            "Для open-вопроса options=null, correct_answers = [\"эталонный ответ\"]. "
            "Для single — ровно 1 правильный вариант, для multiple — все правильные. "
            "ВАЖНО: варианты-дистракторы делай правдоподобными — они должны быть похожи "
            "на правильный по теме/форме, но неверны по смыслу (никакой очевидной абсурдности, "
            "одинаковой длины и стиля с правильным)."
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
        }
    qid = question_id or f"q{len(state.asked_questions) + 1}"
    card = QuizCard(
        question_id=qid,
        question=str(data.get("question", "")).strip(),
        options=data.get("options") if isinstance(data.get("options"), list) else None,
        answer_type=data.get("answer_type") if data.get("answer_type") in ("single", "multiple", "open") else "open",
        difficulty=difficulty,
        topic=str(data.get("topic") or topic),
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
            " Построй урок как хороший учебник: 1) hook — короткий вопрос-зацепка "
            "в 1 предложении, который вызывает интерес; 2) definition — определение темы "
            "в 1-2 простых предложениях; 3) key_terms — 2-4 ключевых термина с краткими "
            "определениями; 4) 2-3 секции, каждая — ОДИН под-вопрос темы: заголовок + "
            "короткое объяснение простыми словами (2-4 предложения) + check_question — один "
            "вопрос «Проверь себя» на понимание этой секции; 5) summary — итог в 1-2 предложения. "
            "Предложения короткие, без канцелярита, без заголовков-эмодзи. "
            "НЕ выдумывай факты за пределами контекста. "
            "Верни строго JSON: {\"title\": \"...\", \"hook\": \"...\", \"definition\": \"...\", "
            "\"key_terms\": [{\"term\": \"...\", \"definition\": \"...\"}], "
            "\"diagram\": {..., см. ниже}, "
            "\"sections\": [{\"heading\": \"...\", \"body\": \"...\", \"citation\": \"§N или ''\", "
            "\"check_question\": \"...\"}], \"summary\": \"...\"}. "
            "citation — номер параграфа/страницы из контекста, если он виден в источнике, иначе пустая строка."
        )
        + _diagram_grade_hint(grade)
        + (
            " Обязательно добавь diagram — схему-иллюстрацию к теме. "
            "Диаграмма отражает ТОЛЬКО те же факты и термины, что и секции/ключевые термины урока — "
            "никаких новых понятий, чтобы не было противоречий с текстом. "
            "kind: 'flow' (этапы / причина→следствие) | 'cycle' (цикл/круговорот) | "
            "'map' (пространственная схема по координатам). "
            "Формат diagram: {\"kind\": \"flow\"|\"cycle\"|\"map\", \"title\": \"короткий заголовок\", "
            "\"nodes\": [{\"id\": \"n1\", \"label\": \"подпись 1-3 слова\"}], "
            "\"edges\": [{\"source\": \"n1\", \"target\": \"n2\", \"label\": \"подпись стрелки\"}]}. "
            "Для kind='map' каждый узел — объект с координатами на схеме (0..1, лево-верх → право-низ): "
            "{\"id\": \"n1\", \"label\": \"Точка A\", \"x\": 0.7, \"y\": 0.35}. "
            "Для противопоставленных ролей стрелок (тёплое/холодное, причина/следствие и т.п.) "
            "укажи цвет: \"color\": \"warm\" или \"cold\". "
            "2-5 узлов, не больше 6 связей."
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


def _lesson_from_data(data: Dict[str, Any], topic: str) -> Lesson:
    """Строит структурированный Lesson из JSON-ответа LLM (нормализация типов).

    Мусорные/пустые поля отбрасываются — урок никогда не содержит пустых карточек.
    """
    sections: List[LessonSection] = []
    raw_sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    for s in raw_sections[:4]:
        if not isinstance(s, dict):
            continue
        body = str(s.get("body") or "").strip()
        if not body:
            continue
        sections.append(LessonSection(
            heading=str(s.get("heading") or "").strip(),
            body=body,
            citation=str(s.get("citation") or "").strip(),
            source=str(s.get("source") or "").strip(),
            check_question=str(s.get("check_question") or "").strip(),
        ))
    key_terms = []
    raw_terms = data.get("key_terms") if isinstance(data.get("key_terms"), list) else []
    for t in raw_terms[:5]:
        if isinstance(t, dict) and str(t.get("term") or "").strip() and str(t.get("definition") or "").strip():
            key_terms.append({"term": str(t["term"]).strip(), "definition": str(t["definition"]).strip()})
    return Lesson(
        title=str(data.get("title") or topic).strip(),
        hook=str(data.get("hook") or "").strip(),
        definition=str(data.get("definition") or "").strip(),
        key_terms=key_terms,
        diagram=_diagram_from_data(data.get("diagram")),
        sections=sections,
        summary=str(data.get("summary") or "").strip(),
    )


def _repair_lesson_from_text(text: str, topic: str) -> Lesson:
    """Собирает Lesson из сплошного текста (LLM проигнорировал JSON).

    Параграфы (по переводам строк) становятся секциями; первый абзац — определение,
    последний — итог (при 4+ абзацах). Консервативно: не выдумываем заголовки.
    """
    paragraphs = [p.strip() for p in re.split(r"\n+", (text or "").strip()) if p.strip()]
    if len(paragraphs) >= 4:
        return Lesson(
            title=topic,
            definition=paragraphs[0],
            sections=[LessonSection(body=p) for p in paragraphs[1:-1]],
            summary=paragraphs[-1],
        )
    if len(paragraphs) >= 2:
        return Lesson(
            title=topic,
            definition=paragraphs[0],
            sections=[LessonSection(body=p) for p in paragraphs[1:]],
        )
    return Lesson(title=topic, sections=[LessonSection(body=text or "")])


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
    data = parse_llm_json(raw)
    lesson = _lesson_from_data(data, topic)
    # Repair: модель вернула не-JSON / JSON без структуры — собираем из сплошного текста.
    if not lesson.sections and not lesson.definition and not lesson.hook:
        text = str(data.get("text") or "").strip()
        if not text and not data:
            # LLM вернул сплошной текст (не JSON) — используем его напрямую
            text = (raw or "").strip()
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
