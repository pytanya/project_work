"""Граф знаний учебника: GET /graph, related-узлы, выбор темы для подготовки."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.knowledge_graph import KnowledgeGraph

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
    if not title and not kg.to_dict()["nodes"]:
        return {"ok": False, "error": "Граф знаний ещё не построен"}

    session.state = session.state.model_copy(
        update={"active_topic": body.topic_id, "topic": title or session.state.topic}
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
