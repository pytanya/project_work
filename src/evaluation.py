"""
EduTutor — оценка ответа ученика (единая логика).

Используется детерминированным узлом `evaluate_answer_node` (graph.py) и агентом
(инструмент `evaluate_answer` в agent_tools.py), чтобы поведение не различалось:
records, knowledge_map, LinUCB bandit, судья (К-4), объяснение ошибки, wiki, quiz_complete.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import adaptive
from .judge import judge_evaluation
from .states import TutorState
from . import tutor as tutor_mod

logger = logging.getLogger("edututor.evaluation")


def rag_context(store: Any, query: str, state: TutorState, k: int = 3) -> List[str]:
    """RAG-поиск с фильтрами сессии (subject/grade/section активной темы)."""
    if store is None:
        return []
    filters: Dict[str, Any] = {}
    if state.subject:
        filters["subject"] = state.subject
    if state.grade:
        filters["grade"] = state.grade
    try:
        results = store.search(query, k=k, filters=filters or None)
        return [r.chunk.text for r in results]
    except Exception:
        return []


def evaluate_and_record(
    st: TutorState,
    deps: Any,
    card: Any,
    answer: str,
    emit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Tuple[TutorState, str, Optional[Any], Optional[Dict[str, Any]]]:
    """Полная оценка ответа + обновление состояния.

    Возвращает (state, message, judge_result, explanation). Не мутирует вход (копирует).
    """
    st = st.model_copy(deep=True)
    context = rag_context(getattr(deps, "store", None), card.topic if card else "", st, k=3)
    if not context:
        context = ["Нет контекста по теме."]

    graded = tutor_mod.evaluate_answer(
        card.question, answer, context, st, llm_call=getattr(deps, "eval_llm", None)
    )
    tutor_mod.update_knowledge_map(st, card.topic, graded.score)
    if st.bandit is not None:
        # LinUCB: обновляем сыгранную руку и выбираем следующую сложность по контексту
        features = adaptive.bandit_features(st)
        adaptive.update_counters(st, graded.correct)
        played = adaptive.difficulty_arm(card.difficulty)
        st.bandit = adaptive.bandit_update(st.bandit, features, played, graded.score)
        st.difficulty = adaptive.arm_difficulty(
            adaptive.bandit_select(st.bandit, features, current_idx=played)
        )
    else:
        tutor_mod.adjust_difficulty(st, graded.correct)

    # Судья: контракт «оценка ответа ученика» (К-4). Для детерминированной сверки
    # с эталоном (закрытый вопрос, model_used="reference") судить нечего.
    deterministic = graded.model_used == "reference"
    if not deterministic:
        judge_result = judge_evaluation(
            card.question,
            answer,
            {"score": graded.score, "correct": graded.correct, "feedback": graded.feedback},
            judge_call=getattr(deps, "judge_llm", None),
        )
        st.last_judge_score = judge_result.avg_score
    else:
        judge_result = None

    message = f"{'Верно' if graded.correct else 'Ошибка'} (оценка {round(graded.score * 10, 1)}/10)."
    if graded.feedback:
        message += f" {graded.feedback}"
    explanation: Optional[Dict[str, Any]] = None
    if not graded.correct and not deterministic:
        explanation = tutor_mod.explain_error(
            card.question, answer, context, st, llm_call=getattr(deps, "expert_llm", None)
        )
        message += f"\nОбъяснение: {explanation['text']}"
        if explanation["citation"]["paragraph"]:
            message += f"\nЦитата: {explanation['citation']['paragraph']}"
    st.agent_message = message
    st.current_question = None
    st.pending_answer = None

    # Экспорт учителю: заполняем запись вопроса оценкой/судьёй
    if st.records and st.records[-1].get("question_id") == card.question_id:
        st.records[-1].update({
            "student_answer": answer,
            "score01": round(graded.score, 4),
            "correct": graded.correct,
            "feedback": graded.feedback,
            "model_used": graded.model_used,
            "judge_score": judge_result.avg_score if judge_result else None,
        })

    if emit is not None:
        emit("tutor.explanation" if not graded.correct else "system",
             message=message,
             citation=(explanation.get("citation") if explanation else None) if not graded.correct else None)

    # Knowledge Wiki: применяем результат текущего ответа к статье темы (идемпотентно)
    try:
        from .wiki import KnowledgeWiki

        wiki = KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR)
        if st.records and st.records[-1].get("question_id") == card.question_id:
            wiki.apply_record(st, st.records[-1])
            if card and card.topic:
                art = wiki.get(getattr(st, "subject", None) or "общая тема", card.topic)
                if art is None or not (art.body or "").strip():
                    wiki.enrich_body(st, card.topic, context, llm_call=getattr(deps, "tutor_llm", None))
    except Exception as exc:
        logger.warning("Knowledge Wiki per-answer update failed: %s", exc)

    if st.answered_count >= st.num_questions:
        st.quiz_complete = True
        st.session_status = "completed"

    return st, message, judge_result, explanation
