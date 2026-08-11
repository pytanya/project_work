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
