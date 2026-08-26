"""Тесты агентного intake (спека 2.3, 5.4): модель выбирает действие через function calling."""

from __future__ import annotations

import json
import sys

from src.agent_loop import agent_available, looks_like_question, run_intake_agent, run_tutor_agent
from src.graph import GraphDeps, build_graph
from src.llm_client import LLMResponse
from src.states import TutorState


class _Deps:
    settings = None


class FakeAgentLLM:
    """Скриптовый мок: по очереди возвращает заданные LLMResponse."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, messages, tools=None):
        resp = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        return resp


def tc(name, arguments, cid="call_1"):
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def tool_response(*calls):
    return LLMResponse(content=None, tool_calls=list(calls))


def text_response(text):
    return LLMResponse(content=text, tool_calls=None)


def _graph_deps(agent_llm):
    from src.knowledge import NumpyVectorStore

    class Emb:
        def encode(self, texts):
            return [[0.0] * 4] * len(texts)

        def encode_query(self, text):
            return [0.0] * 4

    store = NumpyVectorStore("a", Emb())
    return GraphDeps(embedder=Emb(), store=store, settings=_settings(), agent_llm=agent_llm)


def _settings(agent=True):
    from src.config import Settings

    return Settings(_env_file=None, USE_AGENT_INTAKE=agent, MAX_INTAKE_ITERATIONS=8)


class TestLooksLikeQuestion:
    def test_question_mark(self):
        assert looks_like_question("Какой предмет изучаем?") is True

    def test_question_word_no_mark(self):
        assert looks_like_question("Какой предмет изучаем") is True

    def test_garbage_json_false(self):
        assert looks_like_question('{"question": "x", "options": null}') is False
        assert looks_like_question("") is False


class TestRunIntakeAgent:
    def test_passthrough_when_intake_complete(self):
        """Интake завершён → ответ (например, «да» на подтверждение урока) НЕ съедается."""
        st = TutorState(learner_type="student", subject="география", topic="Атмосфера",
                        has_textbook=False, mode="quiz", pending_answer="да")
        deps = _graph_deps(FakeAgentLLM([]))
        st2, proceed = run_intake_agent(st, deps)
        assert proceed is True
        assert st2.pending_answer == "да"  # ответ не тронут — уйдёт нижестоящему узлу

    def test_agent_asks_first_question(self):
        deps = _graph_deps(FakeAgentLLM([
            tool_response(tc("interview_progress", {}, "c0")),
            text_response("С какого предмета начнём? Например, география?"),
        ]))
        st, proceed = run_intake_agent(TutorState(num_questions=3), deps)
        assert proceed is False
        assert "предмет" in (st.agent_question or "")
        assert st.agent_question.endswith("?")

    def test_agent_fills_all_fields_and_proceeds(self):
        answer = "я в 7 классе, география, тема Атмосфера, учебника нет, хочу квиз"
        deps = _graph_deps(FakeAgentLLM([
            tool_response(tc("extract_intake_fields", {"text": answer}, "c1")),
            tool_response(
                tc("set_intake", {"field": "learner_type", "value": "schoolchild"}, "c2"),
                tc("set_intake", {"field": "grade", "value": "7"}, "c3"),
                tc("set_intake", {"field": "subject", "value": "география"}, "c4"),
                tc("set_intake", {"field": "topic", "value": "Атмосфера"}, "c5"),
                tc("set_intake", {"field": "has_textbook", "value": "нет"}, "c6"),
                tc("set_intake", {"field": "mode", "value": "quiz"}, "c7"),
            ),
            tool_response(tc("interview_progress", {}, "c8")),
            text_response("Всё готово, начинаем занятие."),
        ]))
        st, proceed = run_intake_agent(TutorState(num_questions=3), deps)
        assert proceed is True
        assert st.learner_type == "schoolchild"
        assert st.grade == "7"
        assert st.subject == "география"
        assert st.topic == "Атмосфера"
        assert st.has_textbook is False
        assert st.mode == "quiz"
        assert compute(st) == []

    def test_bad_final_text_falls_back_to_template(self):
        deps = _graph_deps(FakeAgentLLM([
            tool_response(tc("interview_progress", {}, "c0")),
            text_response('{"ok": true, "fields": 3}'),  # не похоже на вопрос
        ]))
        st, proceed = run_intake_agent(TutorState(num_questions=3), deps)
        assert proceed is False
        assert "Для кого" in (st.agent_question or "")  # детерминированный шаблон
        assert st.intake_field == "learner_type"


def compute(st):
    from src.intake import compute_missing

    return compute_missing(st)


class TestAgentAvailable:
    def test_production_agent_available(self):
        # нет ни agent_llm, ни tutor_llm → продакшн использует LLMClient(tutor) с tools
        assert agent_available(_Deps()) is True

    def test_agent_llm_enables(self):
        deps = _graph_deps(FakeAgentLLM([]))
        assert agent_available(deps) is True

    def test_tutor_llm_callable_disables(self):
        deps = _graph_deps(None)
        deps.tutor_llm = lambda m: "x"
        assert agent_available(deps) is False


class TestGraphAgentIntake:
    def test_graph_uses_agent_when_available(self):
        answer = "студент, философия, тема Кант, учебника нет, квиз"
        agent = FakeAgentLLM([
            tool_response(tc("extract_intake_fields", {"text": answer}, "c1")),
            tool_response(
                tc("set_intake", {"field": "learner_type", "value": "student"}, "c2"),
                tc("set_intake", {"field": "subject", "value": "философия"}, "c3"),
                tc("set_intake", {"field": "topic", "value": "Кант"}, "c4"),
                tc("set_intake", {"field": "has_textbook", "value": "нет"}, "c5"),
                tc("set_intake", {"field": "mode", "value": "quiz"}, "c6"),
            ),
            tool_response(tc("route_to_source", {}, "c7")),
            text_response("Готовлю квиз."),
        ])
        deps = _graph_deps(agent)
        deps.settings = _settings(agent=True)
        from src.knowledge import NumpyVectorStore

        class Emb:
            def encode(self, texts):
                return [[0.0] * 4] * len(texts)

            def encode_query(self, text):
                return [0.0] * 4

        deps.store = NumpyVectorStore("g", Emb())

        class _Col:
            def __init__(self, status, sources, texts, message="", failed_reason=""):
                self.status = status
                self.sources = sources
                self.texts = texts
                self.message = message
                self.failed_reason = failed_reason

        # Герметичность: не ходим в реальный поиск/LLM-онтологию за пределами агента.
        # Тест проверяет только intake-цикл агента — источник не нужен.
        deps.source_collector = lambda **kw: _Col("failed", [], [],
                                                  message="mock: пусто", failed_reason="empty_result")
        graph = build_graph(deps)
        res = TutorState.model_validate(graph.invoke({**TutorState(num_questions=3).model_dump(), "pending_answer": answer}))
        # агент заполнил intake → граф пошёл на источник (source_status=None → ждёт источника нет → failed?)
        assert res.learner_type == "student"
        assert res.topic == "Кант"
        assert res.mode == "quiz"


_GEN_Q = '{"question": "Что такое атмосфера?", "options": ["газ", "жидкость"], "answer_type": "single", "topic": "Атмосфера", "correct_answers": ["газ"]}'
_EVAL_OK = '{"score": 8, "correct": true, "feedback": "Верно!", "citation_ok": true}'


def _tutor_deps(agent, gen_llm=_GEN_Q, eval_llm=_EVAL_OK):
    from src.config import Settings
    from src.knowledge import NumpyVectorStore

    class Emb:
        def encode(self, texts):
            return [[0.0] * 4] * len(texts)

        def encode_query(self, text):
            return [0.0] * 4

    store = NumpyVectorStore("t", Emb())
    return GraphDeps(
        embedder=Emb(), store=store,
        settings=Settings(_env_file=None, USE_AGENT_TUTOR=True, MAX_INTAKE_ITERATIONS=8),
        agent_llm=agent,
        tutor_llm=lambda m: gen_llm,
        eval_llm=lambda m: eval_llm,
    )


def _quiz_state():
    return TutorState(
        num_questions=2, learner_type="student", subject="география", topic="Атмосфера",
        has_textbook=False, mode="quiz",
        sources=[{"type": "web", "url": "x"}], collection_id="web", source_status="ready",
    )


class TestRunTutorAgent:
    def test_agent_generates_first_question(self):
        agent = FakeAgentLLM([
            tool_response(tc("generate_quiz", {"topic": "Атмосфера"}, "c1")),
            text_response("Начнём! Первый вопрос по атмосфере:"),
        ])
        deps = _tutor_deps(agent)
        st, final = run_tutor_agent(_quiz_state(), deps)
        assert st.current_question is not None
        assert "атмосфера" in (st.current_question.question or "").lower()
        assert st.records and st.records[-1]["question_id"]
        assert st.asked_questions

    def test_agent_evaluates_and_generates_next(self):
        """Агент оценивает ответ (evaluate_answer) → следующий вопрос (generate_quiz)."""
        deps = _tutor_deps(FakeAgentLLM([
            tool_response(tc("generate_quiz", {"topic": "Атмосфера"}, "c1")),
            text_response("Первый вопрос:"),
        ]))
        st = TutorState.model_validate(run_tutor_agent(_quiz_state(), deps)[0])
        # отвечаем → агент оценивает и даёт следующий вопрос
        deps.agent_llm = FakeAgentLLM([
            tool_response(tc("evaluate_answer", {"answer": "газ"}, "c1")),
            tool_response(tc("generate_quiz", {"topic": "Атмосфера"}, "c2")),
            text_response("Верно! Следующий вопрос:"),
        ])
        st = TutorState.model_validate({**st.model_dump(), "pending_answer": "газ"})
        st_new, final = run_tutor_agent(st, deps)
        assert st_new.correct_count >= 1
        assert st_new.current_question is not None  # следующий вопрос
        assert final

    def test_agent_finishes_session(self):
        deps = _tutor_deps(FakeAgentLLM([
            tool_response(tc("generate_quiz", {"topic": "Атмосфера"}, "c1")),
            text_response("Первый вопрос:"),
        ]))
        st = TutorState.model_validate(run_tutor_agent(_quiz_state(), deps)[0])
        st.answered_count = 2
        deps.agent_llm = FakeAgentLLM([
            tool_response(tc("evaluate_answer", {"answer": "газ"}, "c1")),
            tool_response(tc("finish_session", {}, "c2")),
            text_response("Отлично, завершаем!"),
        ])
        st = TutorState.model_validate({**st.model_dump(), "pending_answer": "газ"})
        st_new, _ = run_tutor_agent(st, deps)
        assert st_new.quiz_complete is True
        assert st_new.session_status == "completed"

    def test_safety_eval_when_agent_skips_evaluate(self):
        """Если модель забыла вызвать evaluate_answer — ответ оценивается детерминированно."""
        deps = _tutor_deps(FakeAgentLLM([
            tool_response(tc("generate_quiz", {"topic": "Атмосфера"}, "c1")),
            text_response("Первый вопрос:"),
        ]))
        st = TutorState.model_validate(run_tutor_agent(_quiz_state(), deps)[0])
        deps.agent_llm = FakeAgentLLM([
            tool_response(tc("generate_quiz", {"topic": "Атмосфера"}, "c2")),            text_response("Продолжаем!"),
        ])
        st = TutorState.model_validate({**st.model_dump(), "pending_answer": "газ"})
        st_new, _ = run_tutor_agent(st, deps)
        assert st_new.correct_count >= 1  # оценка произошла (детерминированно)


class TestGraphAgentTutor:
    def test_graph_agent_quiz_flow(self):
        """Сквозной граф: агент генерирует первый вопрос, ответ оценивается."""
        agent = FakeAgentLLM([
            tool_response(tc("generate_quiz", {"topic": "Атмосфера"}, "c1")),
            text_response("Первый вопрос:"),
        ])
        deps = _tutor_deps(agent)
        deps.on_event = lambda e, d: None
        graph = build_graph(deps)
        st = _quiz_state()
        # граф сразу переходит к квизу (источник готов, тема конкретна)
        res = TutorState.model_validate(graph.invoke(st.model_dump()))
        assert res.current_question is not None
        assert res.asked_questions

    def test_lesson_confirm_passthrough_with_agent_intake(self):
        """Урок показан → «да» на подтверждение не съедается агентом intake → квиз."""
        agent = FakeAgentLLM([
            tool_response(tc("generate_quiz", {"topic": "Атмосфера"}, "c1")),
            text_response("Вопрос:"),
        ])
        deps = _tutor_deps(agent)
        deps.on_event = lambda e, d: None
        graph = build_graph(deps)
        st = _quiz_state()
        st.mode = "lesson"
        st.lesson_text = "Атмосфера — газовая оболочка Земли."
        st.lesson_done = True
        st.agent_question = "Готов(а) перейти к квизу? (да / нет)"
        st.pending_answer = "да"
        res = TutorState.model_validate(graph.invoke(st.model_dump()))
        assert res.lesson_confirmed is True
        assert res.current_question is not None  # квиз начался

    def test_agent_tutor_fallback_generates_next_question(self):
        """Баг #3: агент оценил ответ, но НЕ вызвал generate_quiz → следующий вопрос
        генерируется детерминированно (страховка в agent_tutor_node), квиз не зависает."""
        from src.graph import agent_tutor_node
        from api.schemas import QuizCard

        agent = FakeAgentLLM([
            tool_response(tc("evaluate_answer", {"answer": "воздушная газовая оболочка"}, "c1")),
            text_response("Оценено!"),  # финальный текст БЕЗ generate_quiz
        ])
        deps = _tutor_deps(agent, gen_llm=_GEN_Q, eval_llm=_EVAL_OK)
        events = []
        deps.on_event = lambda e, d: events.append(e)
        state = TutorState(
            num_questions=3, mode="quiz", subject="география", topic="Атмосфера",
            learner_type="student", has_textbook=False,
            sources=[{"type": "web", "url": "x"}], collection_id="web", source_status="ready",
            current_question=QuizCard(
                question_id="q1", question="Что такое атмосфера?", options=None,
                answer_type="open", difficulty="medium", topic="Атмосфера",
            ),
            pending_answer="газ", answered_count=1, correct_count=0,
            asked_questions=["Что такое атмосфера?"],
        )
        res = agent_tutor_node(state, deps)
        st = TutorState.model_validate(res)
        # страховка сработала: ответ оценён + следующий вопрос появился
        assert st.correct_count >= 1
        assert st.current_question is not None
        assert st.current_question.question_id != "q1"
        # quiz.card опубликован для фронтенда
        assert "quiz.card" in events
        # квиз не завис: есть активный вопрос, ждём ответ ученика
        assert not st.quiz_complete


class TestObservability:
    """Roadmap #5.7 (10.2): структурированное логирование действий агента."""

    def test_log_tool_action_ok(self, caplog):
        from src.agent_loop import _log_tool_action

        with caplog.at_level("INFO", logger="edututor.agent"):
            _log_tool_action("rag_search", {"query": "Атмосфера"}, '{"ok": true, "topic": "Атмосфера"}', 42)
        assert any("agent.action tool=rag_search ok=True elapsed_ms=42" in r.message for r in caplog.records)
        assert any("reason=Атмосфера" in r.message for r in caplog.records)

    def test_log_tool_action_error(self, caplog):
        from src.agent_loop import _log_tool_action

        with caplog.at_level("INFO", logger="edututor.agent"):
            _log_tool_action("route_to_source", {}, '{"ok": false, "error": "нет источника"}', 7)
        assert any("tool=route_to_source ok=False" in r.message for r in caplog.records)
        assert any("reason=нет источника" in r.message for r in caplog.records)
