"""
EduTutor — агентный цикл `agent_loop` (спека 2.3, 5.4, 7.3).

Модель выбирает действие через function calling (TOOL_SCHEMAS из agent_tools):
интервью по чек-листу (interview_progress / set_intake / extract_intake_fields /
extract_entities) и делегирование в источник (route_to_source).

Узел агента — ReAct-цикл: LLM → tool_calls → выполнение инструмента → повтор,
пока модель не вернёт финальный текст (вопрос ученику) или intake не завершится.

Фолбэк: если агентная LLM не настроена (только дешёвый Callable tutor_llm) или её
ответ не похож на вопрос — используется детерминированный шаблон next_question,
поэтому существующие потоки (и тесты с мок-LLM) не ломаются.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .agent_tools import TOOL_SCHEMAS, AgentToolContext, execute_agent_tool
from .config import settings as default_settings
from .intake import CHECKLIST_ORDER, INTAKE_QUESTIONS, compute_missing, next_question, validate_intake
from .llm_client import LLMClient, LLMResponse
from .states import TutorState

logger = logging.getLogger("edututor.agent")

MAX_AGENT_STEPS = 6
MAX_AGENT_TIME_SEC = 150  # жёсткий бюджет на один ход агента (стоп-кран при зависании)


def _log_tool_action(tool: str, args: Dict[str, Any], result: str, elapsed_ms: int,
                     step_logger: Any = None) -> None:
    """Наблюдаемость (roadmap #5.7, 10.2): action/reason/status по каждому инструменту.

    step_logger — JsonlStepLogger (JSONL-трассировка запроса с request_id); None — пропуск.
    """
    head = (result or "")[:200]
    ok = False
    reason = ""
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict):
            ok = parsed.get("ok") is True
            if parsed.get("ok") is False:
                reason = str(parsed.get("error") or "")[:160]
            else:
                reason = str(parsed.get("topic") or parsed.get("note") or "")[:160]
    except json.JSONDecodeError:
        # Результат не JSON или обрезан (MAX_TOOL_RESULT_CHARS) — эвристика по префиксу
        ok = '"ok": true' in head and '"ok": false' not in head[:80]
    logger.info(
        "agent.action tool=%s ok=%s elapsed_ms=%d args=%s reason=%s",
        tool, ok, elapsed_ms, json.dumps(args, ensure_ascii=False)[:200], reason,
    )
    if step_logger is not None:
        try:
            step_logger.log_step(
                agent_action="agent.action", tool=tool,
                status="ok" if ok else "error",
                duration=elapsed_ms / 1000.0,
                extra={"args": args, "reason": reason},
            )
        except Exception:
            logger.warning("agent_loop: JSONL-запись шага не удалась", exc_info=True)

INTAKE_AGENT_PROMPT = (
    "Ты — тьютор EduTutor. Ты ведёшь короткое интервью, чтобы собрать данные для учебного "
    "занятия (intake). Поля чек-листа и вопросы к ним:\n"
    "- learner_type — «Ты школьник или студент?»\n"
    "- grade — «Какой у тебя класс?» (только если школьник)\n"
    "- subject — «Какой предмет изучаешь?»\n"
    "- topic — «Какая тема или раздел по этому предмету?» (ВАЖНО: это тема, а НЕ предмет — "
    "не переспрашивай предмет)\n"
    "- has_textbook — «Есть ли учебник по этой теме?»\n"
    "- mode — «Что делаем: урок / квиз / объяснение / глубокий разбор?»\n\n"
    "Правила:\n"
    "1. Сначала посмотри состояние чек-листа (оно приходит в этом же запросе) и вызови "
    "interview_progress для подтверждения.\n"
    "2. Задай ОДИН короткий, живой вопрос ПО ПЕРВОМУ НЕЗАПОЛНЕННОМУ полю. Никогда не "
    "спрашивай про уже заполненные поля.\n"
    "3. Когда ученик ответил — извлеки поля через extract_intake_fields (один ответ может "
    "заполнить несколько полей) и примени их через set_intake.\n"
    "4. Если ученик ответил «не знаю» — не настаивай, переходи к следующему полю.\n"
    "5. Когда набор данных достаточен (validate_intake) — вызови route_to_source.\n"
    "6. Отвечай кратко и по-русски, дружелюбно. Не выдумывай ответы ученика. Финальное "
    "сообщение — это твой вопрос ученику (заканчивай вопросительным знаком)."
)


def _state_context(st: TutorState, pending_answer: Optional[str] = None) -> str:
    """Актуальное состояние чек-листа — инжектится в промпт каждый ход (анти-повтор/путаница).

    pending_answer — ответ ученика в текущем ходе: тогда контекст велит ОБРАБОТАТЬ его,
    а не переспрашивать поля.
    """
    missing = compute_missing(st)
    filled = [f for f in CHECKLIST_ORDER if f not in missing]
    filled_s = ", ".join(f"{f}={getattr(st, f, '')}" for f in filled) or "—"
    missing_s = ", ".join(missing) or "—"
    order = [f for f in CHECKLIST_ORDER if f in missing]
    next_field = order[0] if order else "—"
    parts = [
        "Текущее состояние чек-листа:",
        f"- заполнено: {filled_s}",
        f"- пусто: {missing_s}",
    ]
    if pending_answer:
        parts.append(
            f"Ученик только что ответил: «{pending_answer}». СНАЧАЛА обработай этот ответ: "
            "вызови extract_intake_fields и примени найденные поля через set_intake. "
            "НЕ переспрашивай то, что уже названо в этом ответе."
        )
    else:
        parts.append(f"Теперь задай вопрос ТОЛЬКО про поле: {next_field}")
    parts.append(
        "НЕ спрашивай про уже заполненные поля. ВАЖНО: topic — это тема/раздел по предмету, "
        "а не сам предмет; не путай их."
    )
    return "\n".join(parts)


def looks_like_question(text: Optional[str]) -> bool:
    """Эвристика: похож ли ответ агента на вопрос к ученику (для фолбэка на шаблон)."""
    t = (text or "").strip()
    if not t:
        return False
    if "?" in t:
        return True
    low = t.lower()
    prefixes = (
        "какой", "какая", "какие", "какое", "какую", "каком", "каким",
        "что", "как", "сколько", "есть ли", "кто", "где", "когда", "расскажи",
        "назови", "для кого", "выбери", "напиши", "котор", "чем", "о чём",
    )
    return any(low.startswith(p) for p in prefixes)


def agent_available(deps: Any) -> bool:
    """Доступен ли агентный цикл с function calling.

    - deps.agent_llm задан (мок/инъекция) → да;
    - deps.tutor_llm — это Callable без tool_calls → нет (детерминированный intake);
    - ни того, ни другого → продакшн: создаём LLMClient(role="tutor") с tools → да.
    """
    if getattr(deps, "agent_llm", None) is not None:
        return True
    return getattr(deps, "tutor_llm", None) is None


def _agent_llm_response(
    deps: Any,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    with_tools: bool = True,
) -> Optional[LLMResponse]:
    """Вызов агентной LLM: agent_llm → LLMClient(tutor) с tools."""
    agent_llm = getattr(deps, "agent_llm", None)
    if agent_llm is not None:
        try:
            resp = agent_llm(messages, tools if with_tools else None)
            if resp is not None:
                return resp if isinstance(resp, LLMResponse) else None
        except Exception as exc:
            logger.warning("agent_llm сбой: %s", exc)
            return None
    if with_tools:
        client = LLMClient(role="tutor")
        return client.chat(messages, tools=tools, max_tokens=500, temperature=0.2)
    return None


def run_intake_agent(state: TutorState, deps: Any) -> Tuple[TutorState, bool]:
    """Модельно-управляемое интервью (5.4). Возвращает (state, proceed_to_source).

    - proceed_to_source=True → intake завершён/экстренный старт: передаём на источник.
    - proceed_to_source=False → задан вопрос ученику (agent_question), ждём ответ.

    Вызывается только когда agent_available(deps) — иначе используйте детерминированный intake.
    ВАЖНО: если intake уже завершён — ответ НЕ трогаем: он принадлежит нижестоящему узлу
    (подтверждение урока, ответ на вопрос квиза и т.п.).
    """
    st = state.model_copy(deep=True)
    if not compute_missing(st):
        return st, True

    user_text = st.pending_answer
    st.pending_answer = None

    # Детерминированные предпосылки (как в intake_node): LinUCB bandit
    if st.bandit is None and getattr(deps.settings, "ADAPTIVE_BANDIT", True):
        from . import adaptive

        st.bandit = adaptive.make_bandit()

    # Страховочный детерминированный слой: поля из ответа ученика применяются ВСЕГДА,
    # независимо от того, вызвала ли модель set_intake (модель может просто подтвердить
    # словами, не вызвав инструмент). Агент отвечает за живое общение и следующие вопросы.
    if user_text:
        from .intake import apply_answer, extract_intake_fields

        for field_name, value in extract_intake_fields(user_text).items():
            if value is not None and field_name in compute_missing(st):
                st = apply_answer(st, field_name, value)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": INTAKE_AGENT_PROMPT}]
    if user_text:
        messages.append({"role": "user", "content": user_text})

    ctx = AgentToolContext(state=st, deps=deps)
    final_text: Optional[str] = None
    tools_used = False
    # Контентные инструменты (generate_quiz/lesson/deep_dive) используют tutor_llm,
    # если он задан (мок в тестах), иначе — собственный LLMClient.
    tutor_llm_fn = getattr(deps, "tutor_llm", None)
    if callable(tutor_llm_fn):
        ctx.llm_call = tutor_llm_fn
    on_token_fn = getattr(deps, "on_token", None)
    if callable(on_token_fn):
        ctx.on_token = on_token_fn

    _t0 = time.time()
    for _step in range(MAX_AGENT_STEPS):
        if time.time() - _t0 > MAX_AGENT_TIME_SEC:
            logger.warning("run_intake_agent: исчерпан бюджет %ss (шаг %d)", MAX_AGENT_TIME_SEC, _step)
            break
        # Актуальное состояние чек-листа — в промпт каждый ход (анти-повтор/путаница subject/topic).
        # pending_answer учитывается только в первом ходе (это ответ ученика на предыдущий вопрос).
        pending = user_text if _step == 0 else None
        call_messages = [
            {"role": "system", "content": INTAKE_AGENT_PROMPT + "\n\n" + _state_context(st, pending_answer=pending)},
        ] + messages[1:] if messages and messages[0].get("role") == "system" else messages
        resp = _agent_llm_response(deps, call_messages, tools=TOOL_SCHEMAS)
        if resp is None:
            break  # агентная LLM недоступна — детерминированный путь
        if not resp.tool_calls:
            final_text = (resp.content or "").strip() or None
            break
        tools_used = True
        for tc in resp.tool_calls:
            name = (tc.get("function") or {}).get("name", "")
            try:
                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            _t_tool = time.time()
            result, st = execute_agent_tool(name, args, ctx)
            _log_tool_action(name, args, result, int((time.time() - _t_tool) * 1000),
                             step_logger=getattr(ctx.deps, "step_logger", None))
            ctx.state = st
            messages.append({"role": "assistant", "tool_calls": [tc]})
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})

    missing = compute_missing(st)
    decision = validate_intake(st, max_iterations=deps.settings.MAX_INTAKE_ITERATIONS)

    # Intake завершён или экстренный старт → на источник
    if not missing or decision.decision == "emergency_start":
        st.intake_field = None
        st.agent_question = None
        st.agent_card = None  # карточка заполнена/не нужна — переходим к источнику
        if decision.decision == "emergency_start":
            st.agent_message = decision.warning
        return st, True

    # Нужно спросить ученика. Если агент не дал внятного вопроса — детерминированный шаблон.
    question = final_text
    if not question or (tools_used and not looks_like_question(question)):
        question = next_question(st)
    st.agent_message = None
    st.intake_field = next((f for f in CHECKLIST_ORDER if f in missing), None)
    st.agent_question = question
    st.missing_fields = missing
    return st, False


TUTOR_AGENT_PROMPT = (
    "Ты — тьютор EduTutor во время учебного занятия. Ты помогаешь ученику разобраться "
    "в теме, отвечаешь на вопросы, ведёшь квиз. Ты РАЗГОВАРИВАЕШЬ с учеником — "
    "будь дружелюбным, поддерживающим, кратким.\n\n"
    "Правила:\n"
    "0. УРОК: Если lesson_done=False и mode='lesson' — вызови generate_lesson. "
    "Только ПОСЛЕ показа урока переходи к квизу.\n"
    "1. ОЦЕНКА ОТВЕТА: Если есть активный вопрос и ученик прислал ответ — "
    "ОБЯЗАТЕЛЬНО вызови evaluate_answer. СРАЗУ после evaluate_answer вызови "
    "generate_quiz для следующего вопроса. НЕ останавливайся после оценки!\n"
    "2. ОШИБКА: Если ответ неверный — вызови explain_error, затем generate_quiz.\n"
    "3. СВОБОДНЫЙ ВОПРОС: Если ученик спрашивает по теме (не отвечает на квиз) — "
    "вызови rag_search с запросом ИЗ ВОПРОСА ученика, найди факты и ОТВЕТЬ "
    "НА ЭТОТ КОНКРЕТНЫЙ ВОПРОС (2-6 предложений, своими словами). "
    "НЕ пересказывай весь урок целиком и НЕ запускай квиз, пока ученик не подтвердил готовность.\n"
    "4. УГЛУБЛЕНИЕ: Если ученик просит подробнее — вызови deep_dive.\n"
    "5. ЗАВЕРШЕНИЕ: Если ученик хочет закончить или quiz_complete — вызови finish_session.\n"
    "6. ПЕРВЫЙ ВОПРОС: Квиз запускается ТОЛЬКО когда ученик явно подтвердил готовность "
    "(«да», «готов», «начинаем»). Если активного вопроса нет и ученик задал вопрос или "
    "просит объяснить — сначала ответь на вопрос (правило 3), а не начинай квиз.\n"
    "7. ФИНАЛЬНЫЙ ТЕКСТ: Твой ответ ученику — короткий дружелюбный фидбек. "
    "Если оценил ответ — похвали или подбодри. Если дал новый вопрос — подведи.\n"
    "8. НИКОГДА не отвечай только текстом при наличии активного вопроса и ответа ученика. "
    "ВСЕГДА: evaluate_answer → generate_quiz → текст.\n"
    "9. Не выдумывай ответы ученика. Отвечай по-русски."
    "\n10. При ошибке ученика (evaluate_answer вернул correct=false) НЕ выдавай сразу "
    "правильный ответ: вызови give_hint (уровень 1), затем (при повторной ошибке) "
    "give_hint (уровень 2) или explain_error. Один вопрос — не более двух подсказок."
)


# Подмножество инструментов для режима занятия (без инструментов интервью)
TUTOR_TOOL_NAMES = {
    "evaluate_answer", "generate_quiz", "generate_lesson", "explain_error", "give_hint", "deep_dive", "rag_search", "finish_session",
}
TUTOR_TOOL_SCHEMAS = [s for s in TOOL_SCHEMAS if s.get("function", {}).get("name") in TUTOR_TOOL_NAMES]


def _tutor_context(st: TutorState, deps: Optional[Any] = None) -> str:
    """Состояние занятия для промпта агента (7.3.1)."""
    kg_summary = "—"
    try:
        if deps is not None:
            from .graph import _student_kg

            store = _student_kg(deps)
            if store is not None:
                kg = store.get_knowledge_graph(getattr(st, "student_id", None) or "")
                if kg is not None:
                    weak = [t.topic_id for t in kg.get_weak_topics(subject=st.subject, threshold=0.5)]
                    mastered = [t.topic_id for t in kg.get_mastered_topics(subject=st.subject)]
                    kg_summary = f"освоено: {mastered or '—'}; слабые: {weak or '—'}"
    except Exception:
        pass
    parts = [
        "Состояние занятия:",
        f"- режим: {st.mode or 'quiz'}",
        f"- знания ученика: {kg_summary}",
        f"- урок показан: {'да' if st.lesson_done else 'нет'}",
        f"- отвечено вопросов: {st.answered_count}/{st.num_questions}",
        f"- правильных: {st.correct_count}",
        f"- сложность: {st.difficulty}",
        f"- карта знаний: {st.knowledge_map or '—'}",
    ]
    if st.current_question:
        parts.append(f"- активный вопрос: «{st.current_question.question}»")
    else:
        parts.append("- активного вопроса нет")
    if st.quiz_complete:
        parts.append("- квиз завершён — вызови finish_session")
    if st.agent_message:
        parts.append(f"- последний фидбек: «{st.agent_message[:140]}»")
    if st.lesson_done and not st.current_question and st.answered_count == 0:
        parts.append("- ученик ещё не начал квиз — спроси, готов ли он, или ответь на вопрос по теме")
    return "\n".join(parts)


FREE_Q_SYSTEM = (
    "Ты — тьютор EduTutor. Ученик после урока задал вопрос по теме. "
    "Ответь КОРОТКО и ПО СУЩЕСТВУ (2-6 предложений). "
    "Отвечай на КОНКРЕТНЫЙ вопрос ученика, НЕ пересказывай весь урок. "
    "Опирайся на контекст учебника, если он релевантен вопросу. "
    "Если в контексте нет ответа — используй свои знания, но предупреди: "
    "«Этого нет в учебном материале, но по моим знаниям: …»"
)

_READY_ANSWERS = {
    "да", "давай", "готов", "готова", "начинаем", "поехали", "конечно",
    "yes", "ага", "угу", "ок", "окей", "хорошо", "ну да", "пойдём", "пойдем",
    "можно", "да, готов", "да, давай", "давай начнём", "давай начнем",
    "давай квиз", "перейдём к квизу", "перейдем к квизу", "да, перейдём",
}
_NOT_READY_ANSWERS = {
    "нет", "не", "неа", "no", "ещё нет", "пока нет", "не готов", "не готова",
    "не хочу", "потом", "позже", "нет, спасибо", "стоп",
}
_QUESTION_MARKERS = (
    "?", "расскажи", "объясни", "почему", "как", "что такое", "что значит",
    "чем", "пример", "подробнее", "зачем", "когда", "где", "кто",
)


def _is_ready_to_quiz(text: Optional[str]) -> bool:
    """Понял ли ученик, что переходим к квизу (подтвердил «да»-семейством).

    ВАЖНО: если фраза содержит "давай" + другое действие (разбор/вопрос/объяснение/материал),
    это НЕ подтверждение готовности к квизу — агент должен обработать запрос.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    # Вопрос/просьба об объяснении — это НЕ подтверждение готовности
    if any(m in t for m in _QUESTION_MARKERS):
        return False
    t = t.rstrip("!., ").strip()
    if t in _READY_ANSWERS:
        return True
    if t.startswith("да"):
        # "да, давай квиз" — ок, но "да, давай разберём" — нет
        # Проверяем: если после "да" есть другие действия, это не квиз
        remaining = t[2:].strip()
        if remaining and any(w in remaining for w in (
            "разбор", "вопрос", "объясн", "материал", "подробн", "глубок",
            "расскаж", "покаж", "покажи", "узна", "покажи урок"
        )):
            return False
        return True
    # «давай квиз», «спасибо, давай квиз», «ну давай начнём» и т.п.
    if ("готов" in t or "давай" in t) and ("квиз" in t or "перейт" in t or "начн" in t):
        # Но если есть другие действия — не квиз
        if any(w in t for w in (
            "разбор", "вопрос", "объясн", "материал", "подробн", "глубок",
            "расскаж", "покаж", "покажи", "узна", "покажи урок"
        )):
            return False
        return True
    return False


def _is_not_ready(text: Optional[str]) -> bool:
    t = (text or "").strip().lower().rstrip("!., ").strip()
    return t in _NOT_READY_ANSWERS or t.startswith("не готов")


def _looks_like_agent_command(text: Optional[str]) -> bool:
    """Запрос содержит команду для агента (не вопрос и не подтверждение/отказ).

    Такие запросы должны обрабатываться run_tutor_agent (ReAct-цикл),
    который вызовет нужный инструмент (deep_dive, generate_lesson и т.п.).
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    # Прямые команды: "глубокий разбор", "сделай урок", "покажи урок", "объясни тему"
    agent_commands = (
        "глубок", "разбор", "deep dive", "deep_dive",
        "сделай урок", "покажи урок", "начни урок", "сделай объяснение",
        "объясни тему", "расскажи о теме", "покажи материал",
    )
    if any(cmd in t for cmd in agent_commands):
        return True
    # "давай" + действие (не квиз)
    if "давай" in t and any(w in t for w in (
        "разбор", "вопрос", "объясн", "материал", "подробн", "глубок",
        "расскаж", "покаж", "покажи", "узна", "покажи урок"
    )):
        return True
    return False


def _looks_like_free_question(text: Optional[str]) -> bool:
    """Свободный вопрос/просьба ученика (а не подтверждение «да» или «нет»)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if "?" in t:
        return True
    if _is_ready_to_quiz(t) or _is_not_ready(t):
        return False
    if any(m in t for m in (
        "расскажи", "объясни", "почему", "как ", "как?", "что такое", "что значит",
        "зачем", "пример", "подробнее", "повтори", "непонят", "не понятно",
        "уточни", "поясни", "напомни",
    )):
        return True
    # Осмысленная фраза из 3+ слов — скорее вопрос/просьба, чем «да»/«нет»
    return len(t.split()) >= 3


def _tutor_llm_text(deps: Any, messages: List[Dict[str, Any]], timeout_sec: float = 30.0) -> str:
    """Вызов тьютор-LLM одним текстовым ходом (без function calling).

    timeout_sec: максимальное время ожидания ответа (по умолчанию 30с).
    """
    import concurrent.futures

    fn = getattr(deps, "tutor_llm", None)
    if callable(fn):
        return (fn(messages) or "").strip()
    from .llm_client import LLMClient

    def _chat():
        return (LLMClient(role="tutor").chat(messages, temperature=0.3, max_tokens=300).content or "").strip()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_chat)
            return future.result(timeout=timeout_sec)
    except concurrent.futures.TimeoutError:
        logger.warning("_tutor_llm_text: timeout %ss", timeout_sec)
        return ""
    except Exception:
        return ""


def _answer_free_question(st: TutorState, deps: Any, text: str) -> str:
    """Детерминированный ответ на свободный вопрос ученика (RAG → тьютор-LLM).

    Не запускает квиз и не пересказывает урок: rag_search по тексту вопроса,
    затем короткий ответ на КОНКРЕТНЫЙ вопрос.
    """
    from .agent_tools import AgentToolContext, _rag_results

    try:
        results = _rag_results(AgentToolContext(state=st, deps=deps), text, k=4)
    except Exception as exc:
        logger.warning("_answer_free_question: RAG failed (%s)", exc)
        results = []

    if not results:
        # RAG пуст — отвечаем на основе знаний модели (без контекста учебника)
        topic = st.topic or st.subject or ""
        messages = [
            {"role": "system", "content": FREE_Q_SYSTEM},
            {"role": "user", "content": (
                f"Вопрос ученика: {text}\n\nТема: {topic}\n"
                "Контекст учебника: (не найден — ответь на основе своих знаний, "
                "предупредив что этого нет в загруженных материалах)"
            )},
        ]
        try:
            answer = _tutor_llm_text(deps, messages, timeout_sec=30.0)
        except Exception:
            answer = ""
        if len(answer) < 10:
            return (
                "Этого нет в загруженных учебных материалах. "
                "Попробуйте загрузить учебник по теме или уточните вопрос."
            )
        return answer

    context = "\n---\n".join(r.chunk.text for r in results)[:4000]
    topic = st.topic or st.subject or ""
    messages = [
        {"role": "system", "content": FREE_Q_SYSTEM},
        {"role": "user", "content": f"Вопрос ученика: {text}\n\nТема: {topic}\nКонтекст учебника:\n{context}"},
    ]
    try:
        answer = _tutor_llm_text(deps, messages, timeout_sec=30.0)
    except Exception as exc:
        logger.warning("_answer_free_question: LLM failed (%s)", exc)
        answer = ""
    if len(answer) < 10:
        answer = context[:300]
    return answer


def run_tutor_agent(state: TutorState, deps: Any) -> Tuple[TutorState, Optional[str]]:
    """Агент в квизе (спека 7.3.1): ReAct-цикл, модель выбирает следующее действие.

    Возвращает (state, final_text). Страховка: если был активный вопрос и ответ, но модель
    не вызвала evaluate_answer — оцениваем детерминированно (поведение не деградирует).
    """
    from .evaluation import evaluate_and_record

    st = state.model_copy(deep=True)
    user_text = st.pending_answer
    st.pending_answer = None
    had_question = st.current_question is not None
    evaluated = False

    emit = _make_emit(deps)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": TUTOR_AGENT_PROMPT}]
    if user_text:
        messages.append({"role": "user", "content": user_text})

    ctx = AgentToolContext(state=st, deps=deps)
    final_text: Optional[str] = None
    # Контентные инструменты используют tutor_llm (мок в тестах), иначе — LLMClient
    tutor_llm_fn = getattr(deps, "tutor_llm", None)
    if callable(tutor_llm_fn):
        ctx.llm_call = tutor_llm_fn
    on_token_fn = getattr(deps, "on_token", None)
    if callable(on_token_fn):
        ctx.on_token = on_token_fn

    _t0 = time.time()
    for _step in range(MAX_AGENT_STEPS):
        if time.time() - _t0 > MAX_AGENT_TIME_SEC:
            logger.warning("run_tutor_agent: исчерпан бюджет %ss (шаг %d)", MAX_AGENT_TIME_SEC, _step)
            break
        call_messages = [
            {"role": "system", "content": TUTOR_AGENT_PROMPT + "\n\n" + _tutor_context(st, deps)},
        ] + messages[1:]
        resp = _agent_llm_response(deps, call_messages, tools=TUTOR_TOOL_SCHEMAS)
        if resp is None:
            break
        if not resp.tool_calls:
            final_text = (resp.content or "").strip() or None
            logger.info("run_tutor_agent: модель дала финальный ответ (%d символов)", len(final_text or ""))
            break
        for tc in resp.tool_calls:
            name = (tc.get("function") or {}).get("name", "")
            if name == "evaluate_answer":
                evaluated = True
            try:
                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            _t_tool = time.time()
            result, st = execute_agent_tool(name, args, ctx)
            _log_tool_action(name, args, result, int((time.time() - _t_tool) * 1000),
                             step_logger=getattr(ctx.deps, "step_logger", None))
            ctx.state = st
            messages.append({"role": "assistant", "tool_calls": [tc]})
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})

    # Страховка: ответ на активный вопрос так и не оценён моделью → детерминированно
    if had_question and user_text and not evaluated and st.current_question is not None:
        card = st.current_question
        st, message, _j, _e = evaluate_and_record(st, deps, card, user_text, emit=emit)
        st.agent_message = message

    if final_text and not st.quiz_complete:
        if st.agent_message:
            st.agent_message = f"{st.agent_message}\n\n{final_text}"
        else:
            st.agent_message = final_text
    return st, final_text


def _make_emit(deps: Any) -> Callable[[str, Dict[str, Any]], None]:
    """Обёртка on_event для инструментов/оценки (event, **data)."""
    on_event = getattr(deps, "on_event", None)

    def emit(event: str, **data: Any) -> None:
        if on_event is not None:
            try:
                on_event(event, data)
            except Exception:
                pass

    return emit
