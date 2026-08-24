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
    "Ты — тьютор EduTutor во время учебного занятия. Ученик отвечает на вопросы квиза и "
    "может задавать свободные вопросы по теме. Ты сам выбираешь следующее действие через "
    "инструменты.\n\n"
    "Правила:\n"
    "0. ПРОВЕРКА: Если lesson_done=False и lesson_text пуст — ОБЯЗАТЕЛЬНО сначала вызови "
    "generate_lesson для подготовки материала урока. Только ПОСЛЕ урока вызывай generate_quiz "
    "для следующего вопроса.\n"
    "1. Если есть активный вопрос и ученик прислал ответ — сначала вызови evaluate_answer, "
    "чтобы оценить ответ (это обновит записи, мастерство и сложность).\n"
    "2. После оценки: если ответ неверный и уместно — вызови explain_error (объяснение с "
    "цитатой). Затем вызови generate_quiz для следующего вопроса.\n"
    "3. Если ученик задаёт свободный вопрос по теме — вызови rag_search и ответь по контексту "
    "учебника (не выдумывай).\n"
    "4. Если ученик просит объяснить подробнее/глубже — вызови deep_dive.\n"
    "5. Если ученик хочет закончить, либо вопросов больше нет (quiz_complete) — вызови "
    "finish_session.\n"
    "6. Если активного вопроса нет и lesson_done=True — вызови generate_quiz, чтобы дать следующий вопрос.\n"
    "7. Финальное сообщение — твой ответ ученику: короткий фидбек и/или следующий вопрос "
    "(заканчивай вопросительным знаком). Не выдумывай ответы ученика."
)


# Подмножество инструментов для режима занятия (без инструментов интервью)
TUTOR_TOOL_NAMES = {
    "evaluate_answer", "generate_quiz", "generate_lesson", "explain_error", "deep_dive", "rag_search", "finish_session",
}
TUTOR_TOOL_SCHEMAS = [s for s in TOOL_SCHEMAS if s.get("function", {}).get("name") in TUTOR_TOOL_NAMES]


def _tutor_context(st: TutorState) -> str:
    """Состояние занятия для промпта агента (7.3.1)."""
    parts = [
        "Состояние занятия:",
        f"- отвечено вопросов: {st.answered_count}/{st.num_questions}",
        f"- правильных: {st.correct_count}",
        f"- сложность: {st.difficulty}",
        f"- карта знаний: {st.knowledge_map or '—'}",
    ]
    if st.current_question:
        parts.append(f"- активный вопрос: «{st.current_question.question}»")
    if st.quiz_complete:
        parts.append("- квиз завершён — вызови finish_session")
    if st.agent_message:
        parts.append(f"- последний фидбек: «{st.agent_message[:140]}»")
    return "\n".join(parts)


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
            {"role": "system", "content": TUTOR_AGENT_PROMPT + "\n\n" + _tutor_context(st)},
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
