"""Intake: ответ на чек-лист + статус (раздел 8.1, В-4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.schemas import IntakeStatusResponse

from ..deps import get_session, get_store
from ..engine import SessionStore, intake_status, run_step

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["intake"])


class IntakeAnswer(BaseModel):
    answer: str


@router.post("/intake", response_model=IntakeStatusResponse)
async def post_intake(session_id: str, body: IntakeAnswer, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    session.history.append({"role": "user", "text": body.answer, "kind": "intake"})
    await run_step(session, answer=body.answer)
    return intake_status(session.state)


@router.get("/intake/status", response_model=IntakeStatusResponse)
def intake_status_endpoint(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return intake_status(session.state)
