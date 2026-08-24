"""
EduTutor — CLI-демо (MVP, раздел 15.0).

Консольный прогон сценария: intake → источник → квиз → оценка → судья.

Примеры:
    python main.py --scenario schoolchild_grade6_geography      # интерактивно
    python main.py --scenario schoolchild_grade6_geography --auto --questions 3
    python main.py --scenario student_with_pdf --mock           # офлайн-демо
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import settings as default_settings  # noqa: E402
from src.graph import GraphDeps, build_graph  # noqa: E402
from src.guardrails import BudgetGuard, guard_user_input  # noqa: E402
from src.logging_setup import print_panel, setup_logging  # noqa: E402
from src.metrics import MetricsCollector  # noqa: E402
from src.states import TutorState  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
GOLDEN_SET = BASE_DIR / "evals" / "golden_set.json"


def _load_scenario(scenario_id: str) -> Dict:
    data = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    for sc in data["scenarios"]:
        if sc["id"] == scenario_id:
            return sc
    raise SystemExit(f"Неизвестный сценарий: {scenario_id}. Доступные: "
                     + ", ".join(s["id"] for s in data["scenarios"]))


def _build_deps(metrics: MetricsCollector, mock: bool, step_logger=None) -> GraphDeps:
    if mock:
        from evals.edututor_eval import build_mock_deps

        deps = build_mock_deps(default_settings)
        if step_logger is not None:
            deps.step_logger = step_logger
        return deps
    from src.llm_client import LLMClient

    budget = BudgetGuard(default_settings)
    embedder, store = _make_live_store()
    tutor = LLMClient(role="tutor", metrics=metrics, budget=budget)
    cheap = LLMClient(role="cheap", metrics=metrics, budget=budget)
    expert = LLMClient(role="expert", metrics=metrics, budget=budget)
    judge = LLMClient(role="judge", metrics=metrics, budget=budget)
    agent_tutor = LLMClient(role="tutor", metrics=metrics, budget=budget)
    deps = GraphDeps(
        embedder=embedder,
        store=store,
        settings=default_settings,
        tutor_llm=lambda m: tutor.chat(m, temperature=0.3, max_tokens=512).content or "",
        eval_llm=lambda m: cheap.chat(m, temperature=0.0, max_tokens=300).content or "",
        expert_llm=lambda m: expert.chat(m, temperature=0.2, max_tokens=500).content or "",
        judge_llm=lambda m: judge.chat(m, temperature=0.0, max_tokens=200).content or "",
        # Агентный цикл (function calling): модель выбирает действие через TOOL_SCHEMAS
        agent_llm=lambda msgs, tools=None: agent_tutor.chat(msgs, tools=tools, max_tokens=500, temperature=0.2),
    )
    if step_logger is not None:
        deps.step_logger = step_logger
    return deps


def _make_live_store():
    from src.knowledge import make_collection_name, make_embedder, make_store

    embedder = make_embedder(default_settings)
    collection = make_collection_name(embedder, prefix="edututor_cli")
    store = make_store(collection, embedder, persist_dir=Path(default_settings.CHROMA_PERSIST_DIR), settings=default_settings)
    return embedder, store


def _invoke(graph, state_dict):
    return TutorState.model_validate(graph.invoke(state_dict))


def _demo_auto_answer(current: TutorState, scenario: Dict) -> str:
    """Ответы для --auto: сначала чек-лист, затем квиз."""
    # сканированный учебник: ждём страницы + тему (auto: контентные страницы после обложки)
    if current.textbook_scanned and not current.textbook_pages:
        topic = current.topic or current.subject or "тема"
        return f"5-7, {topic}"
    field = current.intake_field
    if field:
        # ищем ответ в intake_answers сценария, иначе универсальный
        canned = scenario.get("intake_answers", [])
        if canned:
            return canned.pop(0)
        return "да"
    # ответ на квиз
    return "Я думаю, что это связано с атмосферой и воздушной оболочкой Земли."


def run(cli_args) -> int:
    # legacy Windows-консоль: UTF-8, чтобы rich не падал на не-cp1251 символах
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    scenario = _load_scenario(cli_args.scenario)
    run_info = setup_logging(BASE_DIR / "output" / "run_cli", session_id=f"sess_{scenario['id']}")
    metrics = MetricsCollector()
    metrics.start()
    deps = _build_deps(metrics, mock=cli_args.mock, step_logger=run_info["step_logger"])
    graph = build_graph(deps)

    state = TutorState(
        num_questions=cli_args.questions or scenario.get("num_questions", 3),
        learner_type=scenario.get("learner_type"),
        grade=cli_args.grade or scenario.get("grade"),
        subject=cli_args.subject or scenario.get("subject"),
        topic=cli_args.topic or scenario.get("topic"),
        mode=scenario.get("mode"),
        has_textbook=scenario.get("has_textbook"),
        textbook_author=scenario.get("textbook_author"),
    )
    textbook_file = cli_args.file or scenario.get("textbook_file")
    if textbook_file and not textbook_file.startswith("auto"):
        state.textbook_file = str(textbook_file)
        state.has_textbook = True
        print_panel("Источник", f"Файл: {Path(textbook_file).name}", "ok")
    elif textbook_file == "auto:downloads" and not cli_args.mock:
        from src.source_finder import find_local_textbooks

        local = find_local_textbooks(default_settings, subject=scenario.get("subject"), author=scenario.get("textbook_author"))
        if local:
            state.textbook_file = str(local[0])
            state.has_textbook = True
            print_panel("Источник (Plan B)", f"Локальный PDF: {local[0].name}", "ok")

    print_panel("EduTutor — демо", f"Сценарий: {scenario['description']}", "info")

    auto = cli_args.auto
    res = _invoke(graph, state.model_dump())
    last_message = ""
    last_lesson = ""

    while True:
        # Завершение — выводим финальное сообщение один раз
        if res.quiz_complete or res.session_status in ("completed", "failed"):
            if res.agent_message and res.agent_message != last_message:
                style = "ok" if "Квиз завершён" in res.agent_message else "err"
                print_panel("Итог", res.agent_message, style)
            break

        # урок/разбор показываем (CLI — без WS; структура остаётся в JSON-экспорте)
        if res.lesson_text and res.lesson_text != last_lesson:
            print_panel("Урок", res.lesson_text, "ok")
            last_lesson = res.lesson_text

        # выводим новые сообщения агента
        if res.agent_message and res.agent_message != last_message:
            print_panel("Агент", res.agent_message, "warn")
            last_message = res.agent_message
        if res.agent_question:
            print_panel("Вопрос", res.agent_question, "metric")
            if res.agent_options:
                print("Варианты:", ", ".join(res.agent_options))

        if res.agent_question is None and res.intake_field is None:
            # внутренний шаг (индексация и т.п.) — продолжаем
            res = _invoke(graph, res.model_dump())
            continue

        if auto:
            answer = _demo_auto_answer(res, scenario)
            print(">", answer)
        else:
            answer = input("Вы: ").strip()
            guard = guard_user_input(answer)
            if guard["blocked"]:
                print_panel("Блокировка", guard["message"], "err")
                continue
        res = _invoke(graph, {**res.model_dump(), "pending_answer": answer})

    # Финальный вывод (завершающее сообщение уже выведено в цикле)
    if res.source_status == "failed" and res.agent_message:
        print_panel("Материалы не найдены", res.agent_message, "err")

    metrics.stop()
    print_panel(
        "Метрики",
        json.dumps(
            {
                "elapsed_sec": metrics.elapsed_sec,
                "total_cost_usd": metrics.total_cost,
                "cost_by_role": metrics.cost_by_role,
                "cheap_refusal_rate": metrics.cheap_refusal_rate,
                "num_llm_calls": metrics.num_llm_calls,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "metric",
    )

    # Экспорт для учителя (CSV: вопросы + сводка)
    try:
        from src.export import write_session_exports

        files = write_session_exports(
            res, session_id=run_info["session_id"],
            total_cost_usd=metrics.total_cost, elapsed_sec=metrics.elapsed_sec,
        )
        print_panel("Экспорт для учителя", f"Вопросы: {files['questions']}\nСводка: {files['summary']}", "ok")
    except Exception as e:  # pragma: no cover
        print_panel("Экспорт", f"Ошибка экспорта: {e}", "err")

    run_info["step_logger"].close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="EduTutor — консольное демо (MVP)")
    parser.add_argument("--scenario", default="schoolchild_grade6_geography",
                        help="ID сценария из evals/golden_set.json")
    parser.add_argument("--questions", type=int, default=None, help="число вопросов квиза")
    parser.add_argument("--auto", action="store_true", help="автоответы (без интерактива)")
    parser.add_argument("--mock", action="store_true", help="офлайн-режим (без сети/LLM)")
    parser.add_argument("--file", type=str, default=None, help="путь к учебнику (PDF/DOCX/TXT)")
    parser.add_argument("--subject", type=str, default=None, help="переопределить предмет")
    parser.add_argument("--grade", type=str, default=None, help="переопределить класс")
    parser.add_argument("--topic", type=str, default=None, help="переопределить тему")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
