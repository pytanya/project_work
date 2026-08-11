"""
EduTutorEval — новый eval-модуль (В-6, раздел 10.3).

НЕ адаптация eval_golden.py из research_guard_agent (тот жёстко завязан на
ResearchAgent.run). EduTutorEval прогоняет последовательность шагов сценария
(intake → источник → квиз → оценка → судья) и считает метрики:
intake_success, find_textbook_success, source_failed, judge_score_evaluation,
intent_accuracy (В-9), cheap_refusal_rate, cost_by_role.

Запуск:
    python evals/edututor_eval.py --runs 3 [--scenario ID] [--mock] [--questions N]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.config import settings as default_settings  # noqa: E402
from src.graph import GraphDeps, build_graph  # noqa: E402
from src.knowledge import DocChunk, NumpyVectorStore, make_embedder, make_store  # noqa: E402
from src.metrics import MetricsCollector  # noqa: E402
from src.nlp import classify_intent  # noqa: E402
from src.source_finder import find_local_textbooks  # noqa: E402
from src.states import TutorState  # noqa: E402

GOLDEN_SET = BASE_DIR / "evals" / "golden_set.json"
INTENT_DATASET = BASE_DIR / "evals" / "intent_dataset.json"
RESULTS_DIR = BASE_DIR / "evals"

_GEN = '{"question": "Что такое атмосфера?", "options": null, "answer_type": "open", "topic": "Атмосфера"}'
_EVAL_OK = '{"score": 8, "correct": true, "feedback": "Верно!", "citation_ok": true}'
_EXPL = '{"text": "Атмосфера — газовая оболочка Земли.", "citation": {"paragraph": "§12", "source": "учебник"}}'
_JUDGE = '{"criteria": {"grade_correct": 9, "feedback_ok": 8, "difficulty_fit": 7}}'


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def intent_accuracy(dataset_path: Path = INTENT_DATASET) -> float:
    """В-9: доля совпадений предсказанного интента с эталонным (порог ≥ 0.8)."""
    data = load_json(dataset_path)
    items = data["items"]
    correct = sum(1 for it in items if classify_intent(it["query"]) == it["intent"])
    return round(correct / len(items), 4)


class _FakeEmbedder:
    def __init__(self, model="test"):
        self.model = model

    def _vec(self, text):
        v = [0.0] * 8
        for token in text.lower().split():
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:4], 16)
            v[h % 8] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def encode(self, texts):
        return [self._vec(t) for t in texts]

    def encode_query(self, text):
        return self._vec(text)


def build_mock_deps(settings: Any) -> GraphDeps:
    """Детерминированные зависимости (без сети/LLM) для офлайн-прогона."""
    embedder = _FakeEmbedder()
    store = NumpyVectorStore("eval", embedder)
    store.add([
        DocChunk(
            id="e1",
            text="Параграф 12: Атмосфера. Атмосфера — воздушная оболочка Земли, состоит из азота (78%) и кислорода (21%).",
            section_number="12", section_title="Атмосфера", source="book", subject="география", grade="6",
        )
    ])

    class _Col:
        def __init__(self, status, sources, texts, message="", failed_reason=""):
            self.status = status
            self.sources = sources
            self.texts = texts
            self.message = message
            self.failed_reason = failed_reason

    def fake_collector(**kw):
        subject = (kw.get("subject") or "").lower()
        if "географ" in subject or "атмосфер" in (kw.get("topic") or "").lower():
            return _Col(
                "ready",
                [{"type": "page", "url": "https://ru.wikibooks.org/wiki/geography"}],
                ["Параграф 12: Атмосфера. Атмосфера — воздушная оболочка Земли, состоит из азота и кислорода."],
                message="mock: материалы по теме",
            )
        return _Col("failed", [], [], message="mock: пусто", failed_reason="empty_result")

    return GraphDeps(
        embedder=embedder,
        store=store,
        settings=settings,
        tutor_llm=lambda m: _GEN,
        eval_llm=lambda m: _EVAL_OK,
        expert_llm=lambda m: _EXPL,
        judge_llm=lambda m: _JUDGE,
        source_collector=fake_collector,
    )


def _make_live_client(role: str, metrics: MetricsCollector) -> Any:
    from src.llm_client import LLMClient

    return LLMClient(role=role, metrics=metrics)


def build_live_deps(settings: Any, metrics: MetricsCollector) -> GraphDeps:
    """Реальные зависимости: embedder API/numpy + LLM-клиенты с метриками."""
    embedder = make_embedder(settings)
    store = make_store("edututor_eval", embedder, persist_dir=None, settings=settings)

    tutor = _make_live_client("tutor", metrics)
    cheap = _make_live_client("cheap", metrics)
    expert = _make_live_client("expert", metrics)
    judge = _make_live_client("judge", metrics)

    return GraphDeps(
        embedder=embedder,
        store=store,
        settings=settings,
        tutor_llm=lambda m: tutor.chat(m, temperature=0.3, max_tokens=512).content or "",
        eval_llm=lambda m: cheap.chat(m, temperature=0.0, max_tokens=300).content or "",
        expert_llm=lambda m: expert.chat(m, temperature=0.2, max_tokens=500).content or "",
        judge_llm=lambda m: judge.chat(m, temperature=0.0, max_tokens=200).content or "",
    )


def run_scenario(
    scenario: Dict[str, Any],
    deps: GraphDeps,
    questions: Optional[int] = None,
    mock: bool = False,
) -> Dict[str, Any]:
    """Прогон одного сценария: intake → источник → квиз → оценка → судья."""
    graph = build_graph(deps)
    num_questions = questions or scenario.get("num_questions", 3)

    state = TutorState(
        num_questions=num_questions,
        learner_type=scenario.get("learner_type"),
        grade=scenario.get("grade"),
        subject=scenario.get("subject"),
        topic=scenario.get("topic"),
        mode=scenario.get("mode"),
        has_textbook=scenario.get("has_textbook"),
        textbook_author=scenario.get("textbook_author"),
    )
    textbook_file = scenario.get("textbook_file")
    if textbook_file == "auto:downloads" and not mock:
        local = find_local_textbooks(default_settings, subject=scenario.get("subject"), author=scenario.get("textbook_author"))
        if local:
            state.textbook_file = str(local[0])
            state.has_textbook = True

    def invoke(d):
        return TutorState.model_validate(graph.invoke(d))

    res = invoke(state.model_dump())
    intake_answers = scenario.get("intake_answers", [])
    for ans in intake_answers:
        res = invoke({**res.model_dump(), "pending_answer": ans})

    intake_success = res.intake_field is None  # чек-лист пройден (дальше источник/квиз/фейл)
    # если материал собран — идём в квиз; иначе — source_failed
    if res.source_status == "failed" or res.session_status == "failed":
        return {
            "scenario": scenario["id"],
            "intake_success": intake_success,
            "find_textbook_success": False,
            "source_failed": True,
            "judge_score_evaluation": None,
            "intent_accuracy": None,
            "message": res.agent_message,
        }

    # Квиз: генерируем ответы по числу вопросов
    for _ in range(num_questions + 1):
        if res.quiz_complete:
            break
        if res.current_question is None and res.agent_question:
            # требуется ответ — если нет вопроса, ждём следующего шага
            res = invoke({**res.model_dump(), "pending_answer": "Я думаю, что это связано с атмосферой и воздушной оболочкой."})
            continue
        if res.current_question is not None:
            res = invoke({**res.model_dump(), "pending_answer": "Я думаю, что это связано с атмосферой и воздушной оболочкой."})
            continue
        break

    return {
        "scenario": scenario["id"],
        "intake_success": intake_success,
        "find_textbook_success": res.source_status == "ready",
        "source_failed": res.session_status == "failed",
        "judge_score_evaluation": res.last_judge_score,
        "intent_accuracy": None,
        "quiz_complete": res.quiz_complete,
        "correct_count": res.correct_count,
        "answered_count": res.answered_count,
        "message": res.agent_message,
    }


def run_all(
    runs: int = 1,
    mock: bool = False,
    scenario_id: Optional[str] = None,
    questions: Optional[int] = None,
) -> Dict[str, Any]:
    scenarios = load_json(GOLDEN_SET)["scenarios"]
    if scenario_id:
        scenarios = [s for s in scenarios if s["id"] == scenario_id]

    intent_acc = intent_accuracy()
    results: Dict[str, Any] = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "mock": mock,
        "intent_accuracy": intent_acc,
        "runs": [],
    }

    for run in range(runs):
        metrics = MetricsCollector()
        if mock:
            deps = build_mock_deps(default_settings)
        else:
            deps = build_live_deps(default_settings, metrics)
        run_results = []
        for sc in scenarios:
            r = run_scenario(sc, deps, questions=questions, mock=mock)
            r["cost_by_role"] = metrics.cost_by_role
            r["cheap_refusal_rate"] = metrics.cheap_refusal_rate
            run_results.append(r)
        results["runs"].append({"run": run + 1, "scenarios": run_results})

    # Агрегация по сценариям (стабильность ≥ 2/3 прогонов, В-6)
    agg = {}
    for sc in scenarios:
        key = sc["id"]
        runs_ok = []
        find_ok = []
        for run in results["runs"]:
            for r in run["scenarios"]:
                if r["scenario"] == key:
                    runs_ok.append(r["intake_success"])
                    find_ok.append(r.get("find_textbook_success", False))
        agg[key] = {
            "intake_success_stability": round(sum(runs_ok) / len(runs_ok), 4) if runs_ok else 0.0,
            "find_textbook_success_stability": round(sum(find_ok) / len(find_ok), 4) if find_ok else 0.0,
        }
    results["aggregated"] = agg
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="EduTutorEval (В-6)")
    parser.add_argument("--runs", type=int, default=1, help="число прогонов")
    parser.add_argument("--scenario", type=str, default=None, help="ID сценария из golden_set.json")
    parser.add_argument("--mock", action="store_true", help="офлайн-режим (без сети/LLM)")
    parser.add_argument("--questions", type=int, default=None, help="число вопросов квиза")
    args = parser.parse_args()

    results = run_all(runs=args.runs, mock=args.mock, scenario_id=args.scenario, questions=args.questions)

    print(f"intent_accuracy = {results['intent_accuracy']} (порог 0.8)")
    for run in results["runs"]:
        print(f"\nRun {run['run']}:")
        for r in run["scenarios"]:
            print(
                f"  {r['scenario']}: intake={r['intake_success']} "
                f"find_textbook={r.get('find_textbook_success')} "
                f"source_failed={r.get('source_failed')} "
                f"judge_score={r.get('judge_score_evaluation')} "
                f"cost={r.get('cost_by_role')}"
            )
    print(f"\nАгрегировано: {results['aggregated']}")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"results_{ts}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Результаты: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
