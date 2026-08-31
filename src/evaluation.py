"""
EduTutor — оценка ответа ученика (единая логика).

Используется детерминированным узлом `evaluate_answer_node` (graph.py) и агентом
(инструмент `evaluate_answer` в agent_tools.py), чтобы поведение не различалось:
records, knowledge_map, LinUCB bandit, судья (К-4), объяснение ошибки, wiki, quiz_complete.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import adaptive
from .judge import judge_evaluation
from .scaffold import hint_for as scaffold_hint_for
from .states import TutorState
from . import tutor as tutor_mod

logger = logging.getLogger("edututor.evaluation")


def rag_context(store: Any, query: str, state: TutorState, k: int = 3) -> List[str]:
    """RAG-поиск с фильтрами сессии (subject/grade/section активной темы/ученик)."""
    if store is None:
        return []
    filters: Dict[str, Any] = {}
    if state.subject:
        filters["subject"] = state.subject
    if state.grade:
        filters["grade"] = state.grade
    if getattr(state, "student_id", None):
        filters["student_id"] = state.student_id
    try:
        results = store.search(query, k=k, filters=filters or None)
        return [r.chunk.text for r in results]
    except Exception:
        return []


def _topic_source(store: Any, topic: str, state: TutorState) -> str:
    """Источник темы (URL/учебник) из RAG-чанков — для wiki-статьи (OKF source)."""
    if store is None or not topic:
        return ""
    filters: Dict[str, Any] = {}
    if state.subject:
        filters["subject"] = state.subject
    if state.grade:
        filters["grade"] = state.grade
    if getattr(state, "student_id", None):
        filters["student_id"] = state.student_id
    try:
        for r in store.search(topic, k=3, filters=filters or None):
            src = getattr(r.chunk, "source", "")
            if src:
                return src
    except Exception:
        pass
    return ""


def sync_student_kg(st: TutorState, deps: Any, topic: str) -> None:
    """Идемпотентный пер-ответный синк темы в Student KG (fail-soft)."""
    student_id = getattr(st, "student_id", None) or ""
    if not student_id or not topic:
        return
    try:
        store = getattr(deps, "student_store", None)
        if store is None and deps.settings is not None:
            from .student import StudentStore

            store = StudentStore(root_dir=deps.settings.STUDENTS_DIR)
        if store is None:
            return
        recs = [r for r in (st.records or []) if r.get("topic") == topic and r.get("score01") is not None]
        attempts = len(recs)
        correct = sum(1 for r in recs if r.get("correct"))
        weak = [str(r.get("feedback", "")).strip() for r in recs if not r.get("correct") and r.get("feedback")]
        mastery = st.knowledge_map.get(topic, 0.0)
        store.update_knowledge_graph(
            student_id=student_id,
            subject=getattr(st, "subject", None) or "общая тема",
            wiki_articles=[{
                "topic": topic,
                "subject": getattr(st, "subject", None) or "общая тема",
                "mastery": mastery,
                "attempts": attempts,
                "correct": correct,
                "weak_areas": weak[:3],
                "last_studied": datetime.datetime.now().isoformat(timespec="seconds"),
            }],
        )
    except Exception as exc:
        logger.warning("Student KG live sync failed (topic=%s): %s", topic, exc)


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
    topic = card.topic if card else ""
    context = rag_context(getattr(deps, "store", None), topic, st, k=3)

    # Spaced repetition: оценка review-карточки (qid "review:<card_id>")
    if card and str(card.question_id or "").startswith("review:"):
        context = rag_context(getattr(deps, "store", None), topic, st, k=2)
        if not context:
            context = ["Нет контекста по теме."]
        graded = tutor_mod.evaluate_answer(card.question, answer, context, st,
                                           llm_call=getattr(deps, "eval_llm", None))
        from .graph import _review_bank

        bank = _review_bank(deps, st)
        card_id = str(card.question_id or "").split(":", 1)[1]
        if bank is not None:
            bank.review_card(card_id, graded.correct)
        tutor_mod.update_knowledge_map(st, card.topic, graded.score)
        sync_student_kg(st, deps, card.topic)
        st.review_reviewed += 1
        if graded.correct:
            st.review_correct += 1
        st.review_index += 1
        st.agent_message = f"{'Верно!' if graded.correct else 'Ошибка'} ({round(graded.score * 10, 1)}/10)."
        st.current_question = None
        st.pending_answer = None
        if emit is not None:
            emit("system" if graded.correct else "tutor.explanation",
                 message=st.agent_message, citation=None,
                 correct_count=st.correct_count, answered_count=st.answered_count)
        return st, st.agent_message, None, None

    # Wiki-LLM (roadmap #2): к контексту оценки добавляем накопленную wiki-статью темы —
    # межсессионные знания/заметки дополняют RAG-чанки (сверка «с wiki», а не только с чанками).
    # Если RAG пуст — wiki-статья становится единственным эталоном.
    wiki_body: Optional[str] = None
    try:
        from .wiki import KnowledgeWiki

        art = KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR,
                            student_id=getattr(st, "student_id", None) or "").get(
            getattr(st, "subject", None) or "общая тема", topic
        )
        if art and (art.body or "").strip():
            wiki_body = art.body.strip()
    except Exception as exc:
        logger.warning("Wiki-контекст в оценке не добавлен: %s", exc)

    if wiki_body:
        # Wiki-статья дополняет RAG-чанки (межсессионные знания); если RAG пуст — единственный эталон.
        context = (list(context) + [wiki_body]) if context else [wiki_body]
    if not context:
        context = ["Нет контекста по теме."]

    graded = tutor_mod.evaluate_answer(
        card.question, answer, context, st, llm_call=getattr(deps, "eval_llm", None)
    )

    enable_scaffolding = bool(getattr(getattr(deps, "settings", None), "ENABLE_SCAFFOLDING", True))
    max_hints = int(getattr(getattr(deps, "settings", None), "MAX_HINTS_PER_QUESTION", 2))

    def _finalize(correct_final: bool) -> Tuple[TutorState, str, Optional[Any], Optional[Dict[str, Any]]]:
        """Финализация вопроса: учёт в knowledge_map/bandit/счётчиках, wiki, KG, quiz_complete."""
        nonlocal st
        final_score = graded.score
        tutor_mod.update_knowledge_map(st, topic, final_score)
        if st.bandit is not None:
            # LinUCB: обновляем сыгранную руку и выбираем следующую сложность по контексту
            features = adaptive.bandit_features(st)
            adaptive.update_counters(st, graded.correct)
            played = adaptive.difficulty_arm(card.difficulty)
            st.bandit = adaptive.bandit_update(st.bandit, features, played, final_score)
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
                card.question, answer, context, st, llm_call=getattr(deps, "expert_llm", None), on_token=deps.on_token
            )
            message += f"\nОбъяснение: {explanation['text']}"
            if explanation["citation"]["paragraph"]:
                message += f"\nЦитата: {explanation['citation']['paragraph']}"
        st.agent_message = message
        st.current_question = None
        st.pending_answer = None
        st.hint_level = 0
        st.attempts_on_question = 0
        st.retry_question_id = None

        # Экспорт учителю: заполняем запись вопроса оценкой/судьёй
        if st.records and st.records[-1].get("question_id") == card.question_id:
            st.records[-1].update({
                "student_answer": answer,
                "score01": round(graded.score, 4),
                "correct": graded.correct,
                "feedback": graded.feedback,
                "model_used": graded.model_used,
                "judge_score": judge_result.avg_score if judge_result else None,
                "question": card.question if card else "",
                "correct_answer": ", ".join(st.current_answers) or "",
            })

        # Spaced repetition (roadmap): ошибочный вопрос → карточка в ReviewBank
        if not graded.correct:
            try:
                from .graph import _review_bank

                bank = _review_bank(deps, st)
                if bank is not None and st.records and st.records[-1].get("question_id") == card.question_id:
                    rec = dict(st.records[-1])
                    rec.setdefault("subject", getattr(st, "subject", None) or "")
                    bank.add_from_record(rec)
            except Exception as exc:
                logger.warning("Review card add failed: %s", exc)

        if emit is not None:
            emit("tutor.explanation" if not graded.correct else "system",
                 message=message,
                 citation=(explanation.get("citation") if explanation else None) if not graded.correct else None,
                 correct_count=st.correct_count,
                 answered_count=st.answered_count)

        # Knowledge Wiki: применяем результат текущего ответа к статье темы (идемпотентно)
        try:
            from .wiki import KnowledgeWiki

            wiki = KnowledgeWiki(deps.settings.KNOWLEDGE_WIKI_DIR,
                                 student_id=getattr(st, "student_id", None) or "")
            if st.records and st.records[-1].get("question_id") == card.question_id:
                wiki.apply_record(st, st.records[-1])
                if card and card.topic:
                    art = wiki.get(getattr(st, "subject", None) or "общая тема", card.topic)
                    if art is None or not (art.body or "").strip():
                        wiki.enrich_body(st, card.topic, context, llm_call=getattr(deps, "tutor_llm", None))
                    # источник информации (URL/учебник) из RAG-чанков
                    src = _topic_source(deps.store, card.topic, st)
                    if src:
                        wiki.set_source(st, card.topic, src)
        except Exception as exc:
            logger.warning("Knowledge Wiki per-answer update failed: %s", exc)

        # Student Knowledge Graph (roadmap #4): живой синк темы на каждый ответ
        if card and card.topic:
            sync_student_kg(st, deps, card.topic)

        if st.answered_count >= st.num_questions:
            st.quiz_complete = True
            st.session_status = "completed"

        return st, message, judge_result, explanation

    # --- Scaffolding: лестница подсказок (B5). При ошибке вопрос НЕ финализируется,
    # выдаётся quiz.hint (уровень 1 → 2), и route_tutor ждёт повторный ответ.
    retry = (st.retry_question_id == (card.question_id if card else None))
    if enable_scaffolding and card is not None and not graded.correct:
        if not retry and st.hint_level == 0:
            # Первая ошибка: не финализируем, даём подсказку уровня 1
            st.hint_level = 1
            st.attempts_on_question = 1
            st.retry_question_id = card.question_id
            st.pending_answer = None
            hint = card.hint or scaffold_hint_for(card.question, ", ".join(st.current_answers), context, 1)
            st.agent_message = f"Пока неверно. Подсказка: {hint}"
            if st.records and st.records[-1].get("question_id") == card.question_id:
                st.records[-1].update({"student_answer": answer, "score01": 0.0, "correct": False,
                                       "feedback": "Первый неверный ответ — ждём повторную попытку"})
            if emit is not None:
                emit("quiz.hint", question_id=card.question_id, hint=hint, level=1,
                     attempts_left=max_hints - 1, subtask=False)
            return st, st.agent_message, None, None
        if retry and st.hint_level < max_hints:
            # Ещё не исчерпали подсказки
            st.hint_level += 1
            st.attempts_on_question += 1
            st.pending_answer = None
            hint = scaffold_hint_for(card.question, ", ".join(st.current_answers), context, st.hint_level)
            st.agent_message = f"Пока неверно. Подсказка ({st.hint_level}/{max_hints}): {hint}"
            if emit is not None:
                emit("quiz.hint", question_id=card.question_id, hint=hint, level=st.hint_level,
                     attempts_left=max_hints - st.hint_level, subtask=False)
            return st, st.agent_message, None, None

    # Декомпозиция: после исчерпания подсказок у сложной задачи (subtasks) — пошаговый разбор.
    # Ошибка фиксируется в records (score01/correct/feedback), но вопрос НЕ сбрасывается:
    # он остаётся смонтированным, и route_tutor уводит граф на NODE_SUBTASK.
    if enable_scaffolding and card is not None and not graded.correct and getattr(card, "subtasks", None):
        st.pending_answer = None
        st.subtask_queue = list(card.subtasks)
        st.subtask_index = 0
        st.hint_level = 0
        st.attempts_on_question = 0
        st.retry_question_id = None
        st.agent_message = "Разберём по шагам."
        if st.records and st.records[-1].get("question_id") == card.question_id:
            st.records[-1].update({
                "student_answer": answer, "score01": round(graded.score, 4),
                "correct": False, "feedback": graded.feedback or "Неверно — разбираем по шагам",
            })
        return st, st.agent_message, None, None
    return _finalize(graded.correct)
