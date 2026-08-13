"""Граф знаний учебника: GET /graph, related-узлы, выбор темы, OKF-экспорт."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.config import settings as default_settings
from src.knowledge_graph import KnowledgeGraph
from src.okf import emit_okf_bundle, validate_bundle

from ..deps import get_session, get_store
from ..engine import SessionStore, run_step

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["graph"])


class TopicBody(BaseModel):
    topic_id: str


@router.get("/graph")
def get_graph(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    kg = KnowledgeGraph.from_dict(session.state.knowledge_graph)
    return {
        "nodes": kg.to_dict()["nodes"],
        "edges": kg.to_dict()["edges"],
        "active_topic": session.state.active_topic,
        "stats": kg.stats(),
    }


@router.get("/graph/{node_id}/related")
def related_nodes(session_id: str, node_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    kg = KnowledgeGraph.from_dict(session.state.knowledge_graph)
    node = next((n for n in kg.to_dict()["nodes"] if n.get("id") == node_id), None)
    return {
        "node": node,
        "related": kg.neighbors(node_id, max_depth=2),
    }


@router.post("/topic")
async def select_topic(session_id: str, body: TopicBody, store: SessionStore = Depends(get_store)):
    """Подготовка по теме: активируем узел графа, генерируем вопрос по этому разделу."""
    session = get_session(store, session_id)
    kg = KnowledgeGraph.from_dict(session.state.knowledge_graph)
    title = ""
    for n in kg.to_dict()["nodes"]:
        if n.get("id") == body.topic_id:
            title = n.get("title", "")
            break
    if not title:
        raise HTTPException(status_code=404, detail="Тема не найдена в графе знаний")

    session.state = session.state.model_copy(
        update={
            "active_topic": body.topic_id,
            "topic": title or session.state.topic,
            "awaiting_topic": False,
        }
    )
    session.history.append({"role": "system", "text": f"Подготовка по теме: {title or body.topic_id}"})
    await run_step(session)
    return {
        "ok": True,
        "active_topic": session.state.active_topic,
        "title": title,
        "question": session.state.current_question.model_dump() if session.state.current_question else None,
        "next_question": session.state.agent_question or "",
    }


@router.get("/knowledge-package")
def knowledge_package(session_id: str, store: SessionStore = Depends(get_store)):
    """OKF-бандл знаний учебника (index + log + topics/*.md с YAML-frontmatter)."""
    session = get_session(store, session_id)
    out_dir = Path(default_settings.KNOWLEDGE_GRAPH_DIR).parent / "okf" / session_id
    source_name = session.state.textbook_name or (session.state.sources[0].get("path") if session.state.sources else "book")
    bundle = emit_okf_bundle(
        session.state, out_dir, Path(str(source_name)).name,
        subject=session.state.subject, grade=session.state.grade,
        curriculum=session.state.curriculum,
    )
    validation = validate_bundle(bundle)
    return {
        "okf_version": "0.2",
        "dir": str(bundle),
        "conformant": validation["conformant"],
        "errors": validation["errors"],
        "files": validation["files"],
        "index": (bundle / "index.md").read_text(encoding="utf-8"),
    }
