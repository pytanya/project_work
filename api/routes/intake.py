"""Intake: ответ на чек-лист / карточка знакомства + статус (раздел 8.1, В-4)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.schemas import IntakeStatusResponse, WsEvent

from src.guardrails import guard_user_input
from src.intake import apply_intake_card

from ..deps import get_session, get_store
from ..engine import SessionStore, intake_status, run_step

logger = logging.getLogger("edututor.api")

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["intake"])


class IntakeAnswer(BaseModel):
    answer: str


class IntakeCardBody(BaseModel):
    values: Dict[str, Any]


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


@router.post("/intake/card", response_model=IntakeStatusResponse)
async def post_intake_card(session_id: str, body: IntakeCardBody, store: SessionStore = Depends(get_store)):
    """Заполненная карточка знакомства: применить все поля сразу, сохранить профиль.

    Быстрее пошагового Q&A: имя/тип/класс сохраняются персистентно (профиль ученика),
    а Wiki/мастерство/заметки ведутся персонально (namespace student_id).
    """
    session = get_session(store, session_id)

    # Контент-фильтр свободных текстовых полей карточки (имя/предмет/тема)
    free_text = " ".join(
        str(body.values.get(k) or "") for k in ("name", "subject", "topic")
    ).strip()
    guard = guard_user_input(free_text)
    if guard["blocked"]:
        session.queue.put(WsEvent(event="session.error", data={"message": guard["message"]}))
        st = session.state
        return IntakeStatusResponse(
            missing_fields=list(st.missing_fields),
            next_question=guard["message"],
            complete=False,
        )

    st = apply_intake_card(session.state, body.values)

    # Персистентный профиль ученика: имя/тип/класс — на будущие сессии
    if st.student_id:
        try:
            profile = store.student_store.get_or_create(st.student_id)
            if st.student_name:
                profile.name = st.student_name
            if st.learner_type:
                profile.learner_type = st.learner_type
            if st.grade:
                profile.grade = st.grade
            store.student_store.save(profile)
        except Exception as exc:
            logger.warning("Не удалось сохранить профиль ученика: %s", exc)

    session.history.append({"role": "user", "text": "карточка знакомства заполнена", "kind": "intake_card"})
    session.state = st
    await run_step(session)  # чек-лист заполнен → продвигаем граф к источнику
    return intake_status(session.state)


@router.get("/intake/status", response_model=IntakeStatusResponse)
def intake_status_endpoint(session_id: str, store: SessionStore = Depends(get_store)):
    session = get_session(store, session_id)
    return intake_status(session.state)
