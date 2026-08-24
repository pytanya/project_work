"""Intake: ответ на чек-лист + статус (раздел 8.1, В-4)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.schemas import IntakeStatusResponse, WsEvent

from src.guardrails import guard_user_input

from ..deps import get_session, get_store
from ..engine import SessionStore, intake_status, run_step

logger = logging.getLogger("edututor.api")

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["intake"])


class IntakeAnswer(BaseModel):
    answer: str


@router.post("/intake", response_model=IntakeStatusResponse)
async def post_intake(session_id: str, body: IntakeAnswer, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    guard = guard_user_input(body.answer)
    if guard["blocked"]:
        # Защита: не передаём ответ агенту; показываем предупреждение как «следующий вопрос»
        session.history.append({"role": "user", "text": body.answer, "kind": "intake", "blocked": True})
        logger.info("post_intake blocked: reasons=%s", guard["reasons"])
        # WS-событие — фронтенд ждёт WS, а не тело HTTP (иначе баннер не появится)
        session.queue.put(WsEvent(event="session.error", data={"message": guard["message"]}))
        st = session.state
        return IntakeStatusResponse(
            missing_fields=list(st.missing_fields),
            next_question=guard["message"],
            complete=False,
        )
    session.history.append({"role": "user", "text": body.answer, "kind": "intake"})
    await run_step(session, answer=body.answer)
    return intake_status(session.state)


@router.get("/intake/status", response_model=IntakeStatusResponse)
def intake_status_endpoint(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return intake_status(session.state)
