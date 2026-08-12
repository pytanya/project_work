"""Тесты OKF-экспорта знаний учебника (Open Knowledge Format v0.2)."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.knowledge_graph import KnowledgeGraph, PREREQUISITE
from src.okf import emit_okf_bundle, validate_bundle
from src.states import TutorState


def _state_with_graph():
    kg = KnowledgeGraph()
    kg.add_topic("book:x", "Учебник «x»", "book")
    kg.add_topic("sec:x:1", "Урок 1: Россия — наша Родина", "section", section_number="1")
    kg.add_topic("sec:x:2", "Урок 2: Культура и религия", "section", section_number="2")
    kg.add_edge("book:x", "sec:x:1", "part_of")
    kg.add_edge("book:x", "sec:x:2", "part_of")
    kg.add_edge("sec:x:2", "sec:x:1", PREREQUISITE)
    return TutorState(
        knowledge_graph=kg.to_dict(),
        curriculum="ИСТ.6.2",
        knowledge_map={"Урок 1: Россия — наша Родина": 0.65},
    )


class TestEmitBundle:
    def test_bundle_structure(self, tmp_path: Path):
        out = emit_okf_bundle(
            _state_with_graph(), tmp_path, "x.pdf", subject="история", grade="6"
        )
        assert (out / "index.md").exists()
        assert (out / "log.md").exists()
        topics = list((out / "topics").glob("*.md"))
        assert len(topics) == 2

    def test_index_frontmatter(self, tmp_path: Path):
        out = emit_okf_bundle(_state_with_graph(), tmp_path, "x", grade="6")
        text = (out / "index.md").read_text(encoding="utf-8")
        meta = yaml.safe_load(text.split("---")[1])
        assert meta["okf_version"] == "0.2"
        assert meta["type"] == "Index"
        assert meta["grade"] == "6"

    def test_topic_frontmatter_with_relations(self, tmp_path: Path):
        out = emit_okf_bundle(_state_with_graph(), tmp_path, "x")
        # «Урок 2» имеет исходящую связь prerequisite → «Урок 1»
        topic = (out / "topics" / "урок-2-культура-и-религия.md")
        assert topic.exists()
        meta = yaml.safe_load(topic.read_text(encoding="utf-8").split("---")[1])
        assert meta["type"] == "Section"
        assert meta["title"] == "Урок 2: Культура и религия"
        assert meta["curriculum"] == "ИСТ.6.2"
        assert meta.get("relations")
        assert meta["relations"][0]["relation"] == "prerequisite"

    def test_knowledge_map_mastery(self, tmp_path: Path):
        out = emit_okf_bundle(_state_with_graph(), tmp_path, "x")
        for p in (out / "topics").glob("*.md"):
            meta = yaml.safe_load(p.read_text(encoding="utf-8").split("---")[1])
            if "Урок 1" in meta["title"]:
                assert meta["mastery"] == 0.65


class TestValidate:
    def test_conformant(self, tmp_path: Path):
        out = emit_okf_bundle(_state_with_graph(), tmp_path, "x")
        v = validate_bundle(out)
        assert v["conformant"] is True
        assert len(v["files"]) == 4  # index + log + 2 topics

    def test_missing_type_detected(self, tmp_path: Path):
        p = tmp_path / "bad.md"
        p.write_text("---\ntitle: без типа\n---\nтело", encoding="utf-8")
        v = validate_bundle(tmp_path)
        assert v["conformant"] is False
        assert any("type" in e for e in v["errors"])
