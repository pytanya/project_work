"""Тесты логирования EduTutor (Слайс 0)."""

from __future__ import annotations

import json
from pathlib import Path

from src.logging_setup import JsonlStepLogger, mask_sensitive, setup_logging


class TestMaskSensitive:
    def test_api_key_masked(self):
        assert mask_sensitive("ключ sk-abc1234567890abcdefgh") == "ключ sk-***masked***"

    def test_yandex_key_masked(self):
        assert mask_sensitive("AQVN1234567890abcdef") == "AQVN***masked***"

    def test_email_masked(self):
        assert mask_sensitive("mail me: user@example.com") == "mail me: ***@***.***"

    def test_query_api_key_masked(self):
        assert "***masked***" in mask_sensitive("https://x.ru/?api_key=secret&a=1")

    def test_recursive_dict_list(self):
        data = {"url": "https://x.ru?token=abc", "nested": ["user@example.com", "ок"]}
        out = mask_sensitive(data)
        assert "token=***masked***" in out["url"]
        assert out["nested"][0] == "***@***.***"
        assert out["nested"][1] == "ок"

    def test_non_string_passthrough(self):
        assert mask_sensitive(42) == 42
        assert mask_sensitive(None) is None


class TestJsonlStepLogger:
    def test_writes_valid_jsonl_with_session(self, tmp_path: Path):
        path = tmp_path / "run.jsonl"
        logger = JsonlStepLogger(path, request_id="req_1", session_id="sess_1")
        logger.log_step(
            step_num=1,
            agent_action="intake.validate",
            tool=None,
            duration=0.5,
            status="OK",
            tokens=100,
            cost=0.001,
            extra={"topic": "Атмосфера"},
        )
        logger.close()

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["request_id"] == "req_1"
        assert record["session_id"] == "sess_1"
        assert record["agent_action"] == "intake.validate"
        assert record["step_num"] == 1
        assert record["extra"]["topic"] == "Атмосфера"

    def test_extra_is_masked(self, tmp_path: Path):
        path = tmp_path / "run.jsonl"
        logger = JsonlStepLogger(path, request_id="r")
        logger.log_step(step_num=1, agent_action="a", extra={"api_key": "sk-1234567890abcdefghij"})
        logger.close()
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert "***masked***" in record["extra"]["api_key"]

    def test_auto_step_num(self, tmp_path: Path):
        """Без явного step_num — автонумерация (фоновые шаги графа/агента)."""
        path = tmp_path / "run.jsonl"
        logger = JsonlStepLogger(path, request_id="req_x", session_id="sess_x")
        logger.log_step(agent_action="node:intake_node")
        logger.log_step(agent_action="agent.action", tool="rag_search", status="ok")
        logger.log_step(agent_action="node:summary")
        logger.close()
        records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        assert [r["step_num"] for r in records] == [1, 2, 3]
        assert all(r["request_id"] == "req_x" for r in records)
        assert records[1]["agent_action"] == "agent.action"


class TestGraphTracing:
    """JSONL-трассировка: проход узлов графа и действия агента пишутся с request_id."""

    def test_graph_nodes_traced(self, tmp_path: Path):
        from src.agent_loop import _log_tool_action
        from src.config import Settings
        from src.graph import GraphDeps, build_graph
        from src.knowledge import NumpyVectorStore
        from src.logging_setup import JsonlStepLogger
        from src.states import TutorState

        class Emb:
            def encode(self, texts):
                return [[0.0] * 4] * len(texts)

            def encode_query(self, text):
                return [0.0] * 4

        path = tmp_path / "trace.jsonl"
        sl = JsonlStepLogger(path, request_id="req_trace", session_id="sess_trace")
        deps = GraphDeps(
            embedder=Emb(), store=NumpyVectorStore("t", Emb()),
            settings=Settings(_env_file=None, MAX_INTAKE_ITERATIONS=8),
            tutor_llm=lambda m: '{"question": "Что?", "answer_type": "open", "topic": "Т"}',
            step_logger=sl,
        )
        graph = build_graph(deps)
        res = TutorState.model_validate(graph.invoke(TutorState(num_questions=2).model_dump()))
        # агентного цикла нет (tutor_llm — Callable) → детерминированный intake задал вопрос
        assert res.agent_question
        sl.close()

        records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        node_actions = [r["agent_action"] for r in records if r["agent_action"].startswith("node:")]
        assert node_actions, "граф должен логировать проходы узлов"
        assert all(r["request_id"] == "req_trace" for r in records)
        assert all(r["session_id"] == "sess_trace" for r in records)

    def test_agent_tool_writes_jsonl(self, tmp_path: Path):
        from src.agent_loop import _log_tool_action
        from src.logging_setup import JsonlStepLogger

        path = tmp_path / "tool.jsonl"
        sl = JsonlStepLogger(path, request_id="req_t")
        _log_tool_action("rag_search", {"query": "Атмосфера"}, '{"ok": true, "topic": "Атмосфера"}', 42,
                         step_logger=sl)
        sl.close()
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert record["agent_action"] == "agent.action"
        assert record["tool"] == "rag_search"
        assert record["status"] == "ok"
        assert record["request_id"] == "req_t"


class TestSetupLogging:
    def test_returns_expected_keys(self, tmp_path: Path):
        run_dir = tmp_path / "run_dir"
        info = setup_logging(run_dir, session_id="sess_fixed")
        assert info["session_id"] == "sess_fixed"
        assert info["request_id"].startswith("req_")
        assert info["run_dir"] == run_dir
        assert info["run_log"].exists()
        assert info["jsonl_path"].exists()
        info["step_logger"].close()
