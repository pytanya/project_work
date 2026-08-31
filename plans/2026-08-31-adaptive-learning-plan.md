# Адаптивное обучение (Student KG v2 + Scaffolding + SM-2) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Полностью встроить Student Knowledge Graph, добавить лестницу подсказок с декомпозицией (scaffolding) и интервальное повторение SM-2 (Question Bank) в EduTutor.

**Architecture:** Расширяем существующий гибрид: детерминированный FSM (дефолт) + тонкие обёртки для агентного цикла. Логика живёт в `src/student_kg.py`, `src/scaffold.py`, `src/review.py` и хуках в `src/evaluation.py`/`src/graph.py`. Должные карточки и подсказки двигаются по существующим рельсам (`quiz.card`, `evaluate_and_record`). Каждый LLM-вызов имеет детерминированный fallback; флаги `ENABLE_SCAFFOLDING`/`ENABLE_SPACED_REPETITION` отключают подсистемы без изменения текущего поведения.

**Tech Stack:** Python 3.11, Pydantic v2, LangGraph (уже в проекте), pytest, React/Vite (минимальные правки).

**Spec:** `plans/2026-08-31-adaptive-learning-design.md`

## Global Constraints

- Проект на Python 3.11+; запуск тестов: `pytest` (из корня репо, `testpaths=["tests"]`, без маркеров).
- Не добавлять новых зависимостей (никаких новых библиотек).
- Запись файлов — атомарная tmp + rename (паттерн `StudentStore.save`, `src/student.py:132-137`).
- Даты — `datetime.datetime.now().isoformat(timespec="seconds")` (локальное naive время, как в `src/student_kg.py:_now_iso`).
- Все LLM-инъекции — Callable-моки в тестах (паттерн JSON-констант из `tests/test_graph.py:19-23`).
- Все новые фолбэки должны быть чисто rule-based (без LLM), чтобы офлайн-режим (`--mock`) работал.
- Не ломать существующие тесты (~550); новые тесты — файлы `tests/test_scaffold.py`, `tests/test_review.py`, расширения `tests/test_student.py`, `tests/test_graph.py`, `tests/test_api.py`, `tests/test_agent_tools.py`, `tests/test_tutor.py`, `tests/test_schemas.py`.

---

## Phase A — Student Knowledge Graph: доведение и полная интеграция

### Task A1: Починить статусы в `StudentKnowledgeGraph` (set_topic / mark_in_progress)

**Files:**
- Modify: `src/student_kg.py:114-182` (`set_topic`, `mark_in_progress`, `mark_mastered`)
- Test: `tests/test_student.py`

**Interfaces:**
- Consumes: текущие сигнатуры `set_topic`, `mark_in_progress`, `mark_mastered`.
- Produces: `set_topic(..., status: Optional[Literal["not_studied","in_progress","mastered"]] = None)` — при явном `status` не пересчитывается; `mark_in_progress` не даунгрейдит `mastered`.

- [ ] **Step 1: Write failing tests**

Добавить в конец `tests/test_student.py`:

```python
from src.student_kg import StudentKnowledgeGraph

def test_set_topic_respects_explicit_status():
    kg = StudentKnowledgeGraph(student_id="s1")
    ts = kg.set_topic(topic_id="t1", status="in_progress")
    assert ts.status == "in_progress"

def test_mark_in_progress_sets_status():
    kg = StudentKnowledgeGraph(student_id="s1")
    ts = kg.mark_in_progress("t1", title="Тема 1")
    assert ts.status == "in_progress"
    assert ts.attempts == 0

def test_mark_in_progress_does_not_downgrade_mastered():
    kg = StudentKnowledgeGraph(student_id="s1")
    kg.set_topic(topic_id="t1", mastery=0.9, attempts=5, correct=5)  # -> mastered
    ts = kg.mark_in_progress("t1")
    assert ts.status == "mastered"

def test_auto_mastery_requires_status_none():
    kg = StudentKnowledgeGraph(student_id="s1")
    ts = kg.set_topic(topic_id="t1", mastery=0.85, attempts=3, correct=3)
    assert ts.status == "mastered"
    ts = kg.set_topic(topic_id="t1", mastery=0.3, attempts=1, correct=0)
    assert ts.status == "in_progress"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_student.py -q`
Expected: FAIL — `mark_in_progress` даёт `not_studied` (статус пересчитывается), `set_topic` игнорирует `status`.

- [ ] **Step 3: Implement**

В `src/student_kg.py` изменить `set_topic` — добавить параметр `status` в конец сигнатуры (после `curriculum_code`) и уважать его:

```python
    def set_topic(
        self,
        topic_id: str,
        title: str = "",
        subject: str = "",
        mastery: float = 0.0,
        attempts: int = 0,
        correct: int = 0,
        weak_areas: Optional[List[str]] = None,
        relations: Optional[Dict[str, List[str]]] = None,
        curriculum_code: Optional[str] = None,
        status: Optional[STATUS_LITERAL] = None,
    ) -> TopicStatus:
        """Upsert topic status. Если status задан явно — не пересчитываем.
        Иначе авто-переход: attempts>=3 и mastery>=0.8 → mastered; attempts>0 → in_progress."""
        now = _now_iso()
        prev = self.topics.get(topic_id)

        if prev:
            attempts = max(prev.attempts, attempts)
            correct = max(prev.correct, correct)
            weak_areas = list(set(prev.weak_areas + (weak_areas or [])))
            relations = _merge_relations(prev.relations, relations or {})
            curriculum_code = curriculum_code or prev.curriculum_code
            title = title or prev.title
            subject = subject or prev.subject

        if status is None:
            if attempts >= 3 and mastery >= 0.8:
                status = "mastered"
            elif attempts > 0:
                status = "in_progress"
            else:
                status = "not_studied"

        ts = TopicStatus(
            topic_id=topic_id,
            title=title or topic_id,
            subject=subject or prev.subject if prev else subject,
            status=status,
            mastery=round(mastery, 4),
            attempts=attempts,
            correct=correct,
            weak_areas=weak_areas or [],
            last_studied=now,
            relations=relations or {},
            curriculum_code=curriculum_code,
        )
        self.topics[topic_id] = ts
        self.touch()
        return ts
```

Изменить `mark_in_progress` (сохранять `mastered`/`in_progress`, если уже были):

```python
    def mark_in_progress(self, topic_id: str, title: str = "", subject: str = "") -> TopicStatus:
        """Тема начата (урок показан) → in_progress; mastered не даунгрейдим."""
        prev = self.topics.get(topic_id)
        status: STATUS_LITERAL = "in_progress"
        if prev is not None and prev.status == "mastered":
            status = "mastered"
        return self.set_topic(
            topic_id=topic_id,
            title=title,
            subject=subject,
            status=status,
            last_studied=_now_iso(),
        )
```

Изменить `mark_mastered` (передавать статус явно):

```python
    def mark_mastered(self, topic_id: str, mastery: float = 1.0) -> TopicStatus:
        return self.set_topic(topic_id=topic_id, mastery=mastery, status="mastered", last_studied=_now_iso())
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_student.py -q`
Expected: PASS (все 4 новых + существующие).

- [ ] **Step 5: Commit**

```bash
git add src/student_kg.py tests/test_student.py
git commit -m "fix(student_kg): уважать явный status в set_topic; mark_in_progress без даунгрейда mastered"
```

### Task A2: `GraphDeps.student_store` + починка интеграции KG в graph.py

**Files:**
- Modify: `src/graph.py:305-335` (GraphDeps, make_graph_deps), `src/graph.py:1315-1328` (content_node), `src/graph.py:1855-1893` (summary_node)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `StudentStore` (`src/student.py:89`), `StudentStore.update_knowledge_graph(student_id, subject, wiki_articles=None, knowledge_map=None, in_progress_topics=None) -> int`.
- Produces: `GraphDeps.student_store: Optional[Any]`; хелпер `_student_kg(deps) -> Optional[StudentStore]` (fail-soft).

- [ ] **Step 1: Write failing integration test**

В `tests/test_graph.py` добавить (в конец файла, переиспользуя существующие фикстуры `make_settings`/`_make_deps`-стиль из файла):

```python
def test_lesson_marks_kg_in_progress(make_settings, tmp_path):
    from src.student import StudentStore
    from src.states import TutorState
    s = make_settings()
    store = NumpyVectorStore("t", FakeEmbedder())
    store.add([DocChunk(text="Атмосфера — газовая оболочка Земли. Азот 78%, кислород 21%.",
                         metadata={"subject": "география", "grade": "6", "topic": "Атмосфера"})])
    student_store = StudentStore(root_dir=tmp_path / "students")
    deps = GraphDeps(embedder=FakeEmbedder(), store=store, settings=s,
                     student_store=student_store,
                     tutor_llm=lambda m: _GEN, expert_llm=lambda m: _EXPL)
    g = build_graph(deps)
    st = TutorState(student_id="stu_test", learner_type="schoolchild", grade="6",
                    subject="география", topic="Атмосфера", mode="lesson",
                    has_textbook=False, source_status="ready")
    # урок генерируется по RAG → тема помечается in_progress
    res = g.invoke(st.model_dump())
    kg = student_store.get_knowledge_graph("stu_test")
    assert kg is not None
    topic = next((t for t in kg.topics.values() if "Атмосфера" in t.title), None)
    assert topic is not None
    assert topic.status == "in_progress"
```

Перед добавлением — проверить, как в `tests/test_graph.py` строятся deps (фикстура `deps`), и вписать тест в тот же стиль (использовать существующую фикстуру, добавив `student_store=StudentStore(root_dir=tmp_path/"students")`).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph.py::test_lesson_marks_kg_in_progress -q`
Expected: FAIL — либо `AttributeError: 'StudentKnowledgeGraphStore' object has no attribute 'update_knowledge_graph'`, либо KG пуст (мёртвая интеграция).

- [ ] **Step 3: Implement**

В `src/graph.py`:

```python
@dataclass
class GraphDeps:
    # ... существующие поля ...
    student_store: Optional[Any] = None  # StudentStore (профили + KG ученика); fail-soft
```

В `make_graph_deps`:

```python
def make_graph_deps(settings: Any = None) -> GraphDeps:
    s = settings or default_settings
    embedder = make_embedder(s)
    collection = make_collection_name(embedder)
    store = make_store(collection, embedder, persist_dir=Path(s.CHROMA_PERSIST_DIR), settings=s)
    if getattr(s, "HYBRID_RAG", True):
        from .knowledge import HybridVectorStore
        store = HybridVectorStore(store)
    from .student import StudentStore
    return GraphDeps(embedder=embedder, store=store, settings=s, collection_name=collection,
                     student_store=StudentStore(root_dir=s.STUDENTS_DIR))
```

Добавить хелпер рядом с `make_graph_deps`:

```python
def _student_kg(deps: GraphDeps) -> Optional[Any]:
    """StudentStore для KG-обновлений (fail-soft: None при любой ошибке)."""
    try:
        if getattr(deps, "student_store", None) is not None:
            return deps.student_store
        if deps.settings is None:
            return None
        from .student import StudentStore
        return StudentStore(root_dir=deps.settings.STUDENTS_DIR)
    except Exception:
        return None
```

В `content_node` заменить блок `# Student Knowledge Graph (roadmap #4)` (строки 1315-1328):

```python
    # Student Knowledge Graph (roadmap #4): тема начата → in_progress
    student_id = getattr(st, "student_id", None) or ""
    if student_id:
        try:
            store = _student_kg(deps)
            if store is not None:
                store.update_knowledge_graph(
                    student_id=student_id,
                    subject=getattr(st, "subject", None) or "общая тема",
                    in_progress_topics=[{"topic_id": topic, "title": topic,
                                         "subject": getattr(st, "subject", None) or ""}],
                )
        except Exception as exc:
            logger.warning("Student KG mark_in_progress failed: %s", exc)
```

В `summary_node` заменить блок (строки 1869-1893):

```python
    # Student Knowledge Graph (roadmap #4): синхронизация knowledge_map + wiki → KG
    student_id = getattr(st, "student_id", None) or ""
    if student_id:
        try:
            store = _student_kg(deps)
            if store is not None:
                wiki_articles = []
                try:
                    wiki_articles = [a.to_dict() for a in wiki.list_articles(subject)]
                except Exception:
                    pass
                store.update_knowledge_graph(
                    student_id=student_id,
                    subject=subject,
                    wiki_articles=wiki_articles,
                    knowledge_map=km,
                )
                logger.info("Student KG: обновлено тем (student_id=%s, subject=%s)", student_id, subject)
        except Exception as exc:
            logger.warning("Student Knowledge Graph update failed: %s", exc)
```

Также починить риск `NameError` в `summary_node`: инициализировать `wiki = None` перед первым try и использовать `if wiki is not None:` во втором.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph.py -q`
Expected: PASS (новый + все существующие тесты графа).

- [ ] **Step 5: Commit**

```bash
git add src/graph.py tests/test_graph.py
git commit -m "fix(graph): реальная интеграция Student KG (GraphDeps.student_store); починка summary_node"
```

### Task A3: Живые апдейты KG на каждый ответ (evaluation.py)

**Files:**
- Modify: `src/evaluation.py:61-186` (`evaluate_and_record` — добавить вызов в конец), `src/graph.py` (без изменений)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `GraphDeps.student_store`, `TutorState.records`, `knowledge_map`, `student_id`, `subject`.
- Produces: хелпер `sync_student_kg(st, deps, topic) -> None` (fail-soft), вызывается в `evaluate_and_record`.

- [ ] **Step 1: Write failing test**

В `tests/test_graph.py`:

```python
def test_answer_updates_kg_live(make_settings, tmp_path):
    from src.student import StudentStore
    from src.states import TutorState
    s = make_settings()
    store = NumpyVectorStore("t", FakeEmbedder())
    store.add([DocChunk(text="Атмосфера — газовая оболочка Земли. Азот 78%, кислород 21%.",
                         metadata={"subject": "география", "grade": "6", "topic": "Атмосфера"})])
    student_store = StudentStore(root_dir=tmp_path / "students")
    deps = GraphDeps(embedder=FakeEmbedder(), store=store, settings=s, student_store=student_store,
                     tutor_llm=lambda m: _GEN, eval_llm=lambda m: _EVAL_OK)
    g = build_graph(deps)
    st = TutorState(student_id="stu_test", subject="география", topic="Атмосфера",
                    mode="quiz", num_questions=1, source_status="ready")
    st = TutorState(**g.invoke(st.model_dump()))
    st = TutorState(**g.invoke(st.model_dump()))  # вопрос сгенерирован
    card = st.current_question
    answer = card.options[0] if card.options else "верный ответ"
    st = TutorState(**g.invoke({**st.model_dump(), "pending_answer": answer}))
    kg = student_store.get_knowledge_graph("stu_test")
    assert kg is not None
    topic = next((t for t in kg.topics.values() if card.topic == t.topic_id or card.topic in t.title), None)
    assert topic is not None
    assert topic.attempts >= 1
```

Примечание: точный номер `question_id`/topic зависит от мока `_GEN`; тест ищет тему по `card.topic`. Если `_EVAL_OK` не в тесте — переиспользовать существующие константы файла.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph.py::test_answer_updates_kg_live -q`
Expected: FAIL — `topic is None` (KG не обновляется на ответе).

- [ ] **Step 3: Implement**

В `src/evaluation.py` добавить хелпер:

```python
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
```

В `evaluate_and_record`, в конце (после блока Wiki, перед проверкой `answered_count >= num_questions`):

```python
    # Student Knowledge Graph (roadmap #4): живой синк темы на каждый ответ
    if card and card.topic:
        sync_student_kg(st, deps, card.topic)
```

Добавить `import datetime` в `src/evaluation.py` (проверить — если импортирован, не дублировать).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evaluation.py tests/test_graph.py
git commit -m "feat(evaluation): живой синк Student KG на каждый ответ (attempts/correct/mastery/weak_areas)"
```

### Task A4: Relations тем из графа учебника в Student KG

**Files:**
- Modify: `src/student_kg.py` (метод `sync_relations_from_knowledge_graph`)
- Modify: `src/graph.py` (`summary_node` — вызов синка)
- Test: `tests/test_student.py`

**Interfaces:**
- Consumes: `StudentKnowledgeGraph.topics`, граф учебника `{nodes:[{id,title}], edges:[{source,target,type}]}`; константа `PREREQUISITE` из `src/knowledge_graph.py`.
- Produces: `StudentKnowledgeGraph.sync_relations_from_knowledge_graph(nodes, edges) -> int`.

- [ ] **Step 1: Write failing test**

В `tests/test_student.py`:

```python
def test_sync_relations_from_knowledge_graph():
    kg = StudentKnowledgeGraph(student_id="s1")
    kg.set_topic(topic_id="Переменные", status="in_progress")
    kg.set_topic(topic_id="Циклы", status="in_progress")
    nodes = [
        {"id": "n1", "title": "Переменные"},
        {"id": "n2", "title": "Циклы"},
    ]
    edges = [
        {"source": "n2", "target": "n1", "type": "prerequisite"},
        {"source": "n1", "target": "n2", "type": "related"},
    ]
    updated = kg.sync_relations_from_knowledge_graph(nodes, edges)
    assert updated >= 1
    ts = kg.get_topic("Циклы")
    assert "Переменные" in ts.relations.get("prerequisite", [])
    ts2 = kg.get_topic("Переменные")
    assert "Циклы" in ts2.relations.get("related", [])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_student.py::test_sync_relations_from_knowledge_graph -q`
Expected: FAIL — `AttributeError: 'StudentKnowledgeGraph' object has no attribute 'sync_relations_from_knowledge_graph'`.

- [ ] **Step 3: Implement**

В `src/student_kg.py` добавить метод в класс `StudentKnowledgeGraph`:

```python
    def sync_relations_from_knowledge_graph(self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> int:
        """Проставить relations тем по графу учебника (по совпадению title темы и title узла)."""
        by_title: Dict[str, str] = {}
        for n in nodes or []:
            title = str(n.get("title") or "").strip().lower()
            if title:
                by_title[title] = str(n.get("id") or "")
        title_to_topic = {ts.title.strip().lower(): ts.topic_id for ts in self.topics.values() if ts.title}
        updated = 0
        for low_title, topic_id in title_to_topic.items():
            node_id = by_title.get(low_title)
            if not node_id:
                continue
            prereq, related = [], []
            for e in edges or []:
                if e.get("source") != node_id:
                    continue
                target_id = e.get("target")
                target_title = next((str(n.get("title") or "").strip().lower() for n in (nodes or []) if str(n.get("id") or "") == target_id), "")
                target_topic = title_to_topic.get(target_title) if target_title else None
                if not target_topic:
                    continue
                if e.get("type") == "prerequisite":
                    prereq.append(target_topic)
                elif e.get("type") == "related":
                    related.append(target_topic)
            if prereq or related:
                ts = self.topics[topic_id]
                ts.relations = _merge_relations(ts.relations, {"prerequisite": prereq, "related": related})
                updated += 1
        if updated:
            self.touch()
        return updated
```

В `src/graph.py` в `summary_node`, внутри блока Student KG (после `store.update_knowledge_graph(...)`), добавить:

```python
                kg_obj = store.get_knowledge_graph(student_id)
                if kg_obj is not None:
                    kg_obj.sync_relations_from_knowledge_graph(
                        (st.knowledge_graph or {}).get("nodes", []),
                        (st.knowledge_graph or {}).get("edges", []),
                    )
                    store.save_knowledge_graph(student_id, kg_obj)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_student.py tests/test_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/student_kg.py src/graph.py tests/test_student.py
git commit -m "feat(student_kg): relations тем из графа учебника (prerequisite/related)"
```

### Task A5: Mastery-гейт + контекст KG для агента

**Files:**
- Modify: `src/graph.py` (`generate_question_node` — emit гейта на первом вопросе темы)
- Modify: `src/agent_loop.py:315-336` (`_tutor_context` — сводка KG)
- Test: `tests/test_graph.py`, `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `StudentKnowledgeGraph.get_prerequisite_gaps(topic_id)`, `_student_kg(deps)`.
- Produces: WS `system` с `kind="mastery.gate"`; в `_tutor_context` — блок «Знания ученика: ...».

- [ ] **Step 1: Write failing test**

В `tests/test_graph.py`:

```python
def test_mastery_gate_emits_for_unmastered_prereq(make_settings, tmp_path):
    from src.student import StudentStore
    from src.student_kg import StudentKnowledgeGraph
    from src.states import TutorState
    s = make_settings()
    store = NumpyVectorStore("t", FakeEmbedder())
    store.add([DocChunk(text="Переменные — именованная область памяти. Циклы — повторение действий.",
                         metadata={"subject": "информатика", "grade": "7", "topic": "Циклы"})])
    student_store = StudentStore(root_dir=tmp_path / "students")
    kg = StudentKnowledgeGraph(student_id="stu_test", subject="информатика")
    kg.set_topic(topic_id="Переменные", status="in_progress")
    kg.set_topic(topic_id="Циклы", status="in_progress",
                 relations={"prerequisite": ["Переменные"]})
    student_store.save_knowledge_graph("stu_test", kg)
    events = []
    deps = GraphDeps(embedder=FakeEmbedder(), store=store, settings=s, student_store=student_store,
                     tutor_llm=lambda m: _GEN,
                     on_event=lambda ev, data: events.append((ev, data)))
    g = build_graph(deps)
    st = TutorState(student_id="stu_test", subject="информатика", topic="Циклы",
                    mode="quiz", num_questions=1, source_status="ready")
    g.invoke(st.model_dump())
    g.invoke(st.model_dump())  # вопрос по теме сгенерирован
    assert any(ev == "system" and data.get("kind") == "mastery.gate" for ev, data in events)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph.py::test_mastery_gate_emits_for_unmastered_prereq -q`
Expected: FAIL — события `mastery.gate` нет.

- [ ] **Step 3: Implement**

В `src/graph.py` в `generate_question_node`, сразу после вычисления `topic` (строка ~1754), добавить хелпер и вызов:

```python
def _maybe_emit_mastery_gate(st: TutorState, deps: GraphDeps, topic: str) -> None:
    """Mastery-гейт (DeepTutor): если пререквизиты темы не освоены — мягкое предупреждение."""
    student_id = getattr(st, "student_id", None) or ""
    if not student_id or not topic:
        return
    try:
        store = _student_kg(deps)
        if store is None:
            return
        kg = store.get_knowledge_graph(student_id)
        if kg is None:
            return
        gaps = kg.get_prerequisite_gaps(topic)
        if gaps:
            titles = [g for g in gaps]
            _emit(deps, "system",
                  message=f"Совет: прежде чем «{topic}», стоит повторить: {', '.join(titles)}.",
                  kind="mastery.gate", gaps=titles)
    except Exception as exc:
        logger.warning("mastery.gate failed: %s", exc)
```

Вызов в `generate_question_node` после определения `topic` (только один раз на тему — через `len(st.asked_questions) == 0`):

```python
    if len(st.asked_questions) == 0:
        _maybe_emit_mastery_gate(st, deps, topic)
```

В `src/agent_loop.py` в `_tutor_context` добавить блок (в начало `parts`):

```python
    from .graph import _student_kg
    student_id = getattr(st, "student_id", None) or ""
    kg_store = _student_kg(st) if False else None  # заменится ниже
```

Внимание: `_tutor_context` не имеет доступа к `deps`. Изменить сигнатуру на `_tutor_context(st, deps=None)` и в `run_tutor_agent` передавать deps. Реализация блока:

```python
    kg_summary = "—"
    try:
        if deps is not None:
            from .graph import _student_kg
            store = _student_kg(deps)
            if store is not None:
                kg = store.get_knowledge_graph(getattr(st, "student_id", None) or "")
                if kg is not None:
                    weak = [t.topic_id for t in kg.get_weak_topics(subject=st.subject, threshold=0.5)]
                    mastered = [t.topic_id for t in kg.get_mastered_topics(subject=st.subject)]
                    kg_summary = f"освоено: {mastered or '—'}; слабые: {weak or '—'}"
    except Exception:
        pass
    parts = [
        "Состояние занятия:",
        f"- режим: {st.mode or 'quiz'}",
        f"- знания ученика: {kg_summary}",
        ...
    ]
```

Обновить сигнатуру `_tutor_context(st, deps=None)` и все вызовы в `agent_loop.py` (найти `_tutor_context(` и передать deps).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph.py tests/test_agent_loop.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/graph.py src/agent_loop.py tests/test_graph.py tests/test_agent_loop.py
git commit -m "feat: mastery-гейт при выборе темы + сводка Student KG в контексте агента"
```

**Phase A complete.** Прогнать весь набор: `pytest -q`.

---

## Phase B — Scaffolding: лестница подсказок + декомпозиция

### Task B1: Конфиг + схемы (QuizCard.hint/subtasks, WsEvent quiz.hint/review.done)

**Files:**
- Modify: `src/config.py` (новые настройки), `api/schemas.py` (QuizCard, WsEvent)
- Test: `tests/test_config.py`, `tests/test_schemas.py`

**Interfaces:**
- Produces: `Settings.ENABLE_SCAFFOLDING: bool=True`, `MAX_HINTS_PER_QUESTION: int=2`, `ENABLE_SPACED_REPETITION: bool=True`, `REVIEW_QUIZ_SIZE: int=5`, `REVIEW_BANK_MAX_CARDS: int=200`, `REVIEW_BANK_DIR: Path`; `QuizCard.hint/subtasks`; WsEvent literal `quiz.hint`, `review.done`.

- [ ] **Step 1: Write failing tests**

В `tests/test_schemas.py`:

```python
from api.schemas import QuizCard, WsEvent

def test_quiz_card_optional_hint_subtasks():
    card = QuizCard(question_id="q1", question="?", answer_type="open", difficulty="medium", topic="t")
    assert card.hint is None
    assert card.subtasks is None
    card2 = QuizCard(question_id="q2", question="?", answer_type="open", difficulty="hard", topic="t",
                     hint="подумай", subtasks=["шаг1", "шаг2"])
    assert card2.hint == "подумай"
    assert card2.subtasks == ["шаг1", "шаг2"]

def test_ws_event_quiz_hint_and_review_done():
    WsEvent(event="quiz.hint", data={"hint": "x", "level": 1, "attempts_left": 1})
    WsEvent(event="review.done", data={"reviewed": 3, "correct": 2})
```

В `tests/test_config.py`:

```python
def test_adaptive_learning_defaults(make_settings):
    s = make_settings()
    assert s.ENABLE_SCAFFOLDING is True
    assert s.ENABLE_SPACED_REPETITION is True
    assert s.MAX_HINTS_PER_QUESTION == 2
    assert s.REVIEW_QUIZ_SIZE == 5
    assert s.REVIEW_BANK_MAX_CARDS == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_schemas.py tests/test_config.py -q`
Expected: FAIL — атрибутов нет / enum не принимает `quiz.hint`.

- [ ] **Step 3: Implement**

`src/config.py` (рядом с `QUESTION_DEDUPE_*`):

```python
    # --- Адаптивное обучение (roadmap: scaffolding + spaced repetition) ---
    ENABLE_SCAFFOLDING: bool = Field(default=True)
    ENABLE_SPACED_REPETITION: bool = Field(default=True)
    MAX_HINTS_PER_QUESTION: int = Field(default=2)
    REVIEW_QUIZ_SIZE: int = Field(default=5)
    REVIEW_BANK_MAX_CARDS: int = Field(default=200)
    REVIEW_BANK_DIR: Path = Field(default=BASE_DIR / "data" / "review_bank")
```

`api/schemas.py` — `QuizCard`:

```python
class QuizCard(BaseModel):
    question_id: str
    question: str
    options: Optional[List[str]] = None
    answer_type: Literal["single", "multiple", "open"]
    difficulty: Literal["easy", "medium", "hard"]
    topic: str
    hint: Optional[str] = None
    subtasks: Optional[List[str]] = None
    excerpt: Optional[str] = None
```

`WsEvent` Literal: добавить `"quiz.hint"`, `"review.done"` (после `"quiz.card"` и `"tutor.summary"`).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_schemas.py tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/config.py api/schemas.py tests/test_schemas.py tests/test_config.py
git commit -m "feat(schemas): QuizCard.hint/subtasks; WsEvent quiz.hint/review.done; настройки scaffolding/review"
```

### Task B2: Поля scaffolding/review в TutorState

**Files:**
- Modify: `src/states.py:59-132`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Produces: `TutorState.hint_level: int=0`, `attempts_on_question: int=0`, `retry_question_id: Optional[str]=None`, `subtask_queue: Optional[List[str]]=None`, `subtask_index: int=0`, `review_requested: bool=False`, `review_active: bool=False`, `review_cards: List[Dict[str,Any]]=[]`, `review_index: int=0`, `review_reviewed: int=0`, `review_correct: int=0`.

- [ ] **Step 1: Write failing test**

В `tests/test_schemas.py`:

```python
from src.states import TutorState

def test_tutor_state_scaffold_review_fields():
    st = TutorState()
    assert st.hint_level == 0
    assert st.attempts_on_question == 0
    assert st.retry_question_id is None
    assert st.subtask_queue is None
    assert st.review_requested is False
    assert st.review_active is False
    assert st.review_cards == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_schemas.py::test_tutor_state_scaffold_review_fields -q`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

В `src/states.py` в `TutorState`, после `bandit` (строка 132):

```python
    # --- Scaffolding (адаптивное усложнение, roadmap) ---
    hint_level: int = 0
    attempts_on_question: int = 0
    retry_question_id: Optional[str] = None
    subtask_queue: Optional[List[str]] = None
    subtask_index: int = 0

    # --- Spaced repetition (SM-2 Question Bank, roadmap) ---
    review_requested: bool = False
    review_active: bool = False
    review_cards: List[Dict[str, Any]] = Field(default_factory=list)
    review_index: int = 0
    review_reviewed: int = 0
    review_correct: int = 0
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_schemas.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/states.py tests/test_schemas.py
git commit -m "feat(states): поля scaffolding (hint/subtasks) и review (SM-2)"
```

### Task B3: Генерация hint/subtasks в вопросе (tutor.py)

**Files:**
- Modify: `src/tutor.py:287-415` (`_question_prompt`, `generate_question`)
- Test: `tests/test_tutor.py`

**Interfaces:**
- Consumes: `QuizCard.hint/subtasks`.
- Produces: `QuizCard` с заполненными `hint`/`subtasks` из JSON LLM (дефенсивный парсинг; при отсутствии — `None`).

- [ ] **Step 1: Write failing test**

В `tests/test_tutor.py` (использовать существующую фикстуру `make_state`/стиль файла; если её нет — создать `TutorState()`, `FakeEmbedder` и мок `llm_call`):

```python
def test_generate_question_includes_hint_and_subtasks():
    from src.states import TutorState
    state = TutorState(topic="Циклы", difficulty="hard", num_questions=3)
    raw_json = json.dumps({
        "question": "Напиши алгоритм поиска максимума в списке.",
        "options": None,
        "answer_type": "open",
        "topic": "Циклы",
        "correct_answers": ["пройти по элементам и сравнивать с текущим максимумом"],
        "excerpt": "Алгоритм: начать с первого элемента…",
        "hint": "Подумай, с какого значения начинать сравнение.",
        "subtasks": ["Определи начальное значение", "Сравни с каждым элементом", "Обнови максимум"],
    })
    card = tutor_mod.generate_question("Циклы", ["Контекст"], "hard", state, llm_call=lambda m: raw_json)
    assert card.hint == "Подумай, с какого значения начинать сравнение."
    assert card.subtasks == ["Определи начальное значение", "Сравни с каждым элементом", "Обнови максимум"]

def test_generate_question_tolerates_missing_hint():
    from src.states import TutorState
    state = TutorState(topic="Тема", difficulty="medium", num_questions=3)
    raw_json = json.dumps({"question": "Вопрос?", "options": None, "answer_type": "open",
                           "topic": "Тема", "correct_answers": ["ответ"], "excerpt": "текст"})
    card = tutor_mod.generate_question("Тема", ["Контекст"], "medium", state, llm_call=lambda m: raw_json)
    assert card.hint is None
    assert card.subtasks is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_tutor.py::test_generate_question_includes_hint_and_subtasks -q`
Expected: FAIL — `card.hint is None`.

- [ ] **Step 3: Implement**

В `_question_prompt` дополнить JSON-контракт в system-строке (после `"excerpt"`):

```python
            "\"hint\": \"<короткая наводящая подсказка, 1-2 предложения, НЕ раскрывающая ответ>\", "
            "\"subtasks\": [\"<шаг 1>\", \"<шаг 2>\"] или null (заполняй ТОЛЬКО для многошаговых "
            "открытых задач: 1-3 коротких шага разбиения решения)\". "
            "Для простых фактологических вопросов hint краткий (одно слово-подсказка), subtasks=null."
```

В `generate_question`, при сборке `card` (после `excerpt=excerpt`):

```python
        hint_raw = data.get("hint")
        hint = str(hint_raw).strip() if isinstance(hint_raw, str) and hint_raw.strip() else None
        sub_raw = data.get("subtasks")
        subtasks = [str(x).strip() for x in sub_raw if str(x).strip()] if isinstance(sub_raw, list) and sub_raw else None
        if subtasks and len(subtasks) > 3:
            subtasks = subtasks[:3]
        card = QuizCard(
            ...
            hint=hint,
            subtasks=subtasks,
            excerpt=excerpt,
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_tutor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tutor.py tests/test_tutor.py
git commit -m "feat(tutor): генерация hint/subtasks в вопросах (дефенсивный парсинг)"
```

### Task B4: Модуль `src/scaffold.py` — лестница подсказок (rule-based, LLM-опция)

**Files:**
- Create: `src/scaffold.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: `TutorState`.
- Produces: `MAX_HINTS` (из конфига), `hint_for(question, correct_answer, context, level, llm_call=None) -> str`; `subtask_step(queue, index) -> str`.

- [ ] **Step 1: Write failing tests**

`tests/test_scaffold.py`:

```python
import re
from src.scaffold import hint_for, subtask_step

def test_hint_level1_uses_keywords_from_answer():
    hint = hint_for("Что такое атмосфера?", "Газовая оболочка Земли, состоит из азота и кислорода.",
                    ["Газовая оболочка Земли"], level=1)
    assert "оболочк" in hint.lower() or "газов" in hint.lower()

def test_hint_level2_reveals_beginning():
    hint = hint_for("Что такое атмосфера?", "Газовая оболочка Земли, состоит из азота и кислорода.",
                    ["Газовая оболочка Земли"], level=2)
    assert "Газовая оболочка" in hint

def test_hint_level_clamped():
    h1 = hint_for("q", "a b c d e f", ["ctx"], level=0)
    h3 = hint_for("q", "a b c d e f", ["ctx"], level=99)
    assert isinstance(h1, str) and isinstance(h3, str)

def test_hint_fallback_no_correct_answer():
    hint = hint_for("q", "", ["контекст с термином"], level=1)
    assert hint.strip() != ""

def test_subtask_step():
    assert subtask_step(["шаг1", "шаг2"], 0) == "шаг1"
    assert subtask_step(["шаг1"], 5) == "шаг1"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_scaffold.py -q`
Expected: FAIL — `ModuleNotFoundError: src.scaffold`.

- [ ] **Step 3: Implement**

`src/scaffold.py`:

```python
"""EduTutor — scaffolding: лестница подсказок (адаптивное усложнение).

Уровень 1 — наводящая подсказка (ключевой термин/направление, ответ не раскрывается).
Уровень 2 — раскрывающая («начни так: …», первые слова эталонного ответа).
LLM-опция: если передан llm_call — используем его для более мягкой формулировки;
иначе (и при сбое) — детерминированный rule-based fallback (никакой LLM).
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

logger = logging.getLogger("edututor.scaffold")

_STOP = {"правильный", "ответ", "правильно", "ответить", "начни", "начать", "это", "вопрос"}


def _keywords(text: str, limit: int = 3) -> List[str]:
    words = re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", text or "")
    seen, out = set(), []
    for w in words:
        low = w.lower()
        if low not in seen and low not in _STOP:
            seen.add(low)
            out.append(w)
        if len(out) >= limit:
            break
    return out


def hint_for(
    question: str,
    correct_answer: str,
    context: List[str],
    level: int,
    llm_call: Optional[object] = None,
) -> str:
    """Подсказка уровня level (1 или 2). Rule-based по умолчанию; llm_call — опция."""
    level = 1 if level < 1 else (2 if level > 2 else level)
    ca = (correct_answer or "").strip()
    ctx = " ".join(context or []).strip()

    if llm_call is not None:
        try:
            role = "Совет" if level == 1 else "Начни"
            prompt = [
                {"role": "system", "content": "Ты — тьютор. Дай ОДНУ короткую подсказку "
                 f"({role}) для вопроса, НЕ выдавая полный ответ. 1-2 предложения."},
                {"role": "user", "content": f"Вопрос: {question}\nЭталон: {ca[:200]}\nКонтекст: {ctx[:400]}"},
            ]
            raw = llm_call(prompt)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()[:280]
        except Exception as exc:
            logger.warning("hint_for: LLM-подсказка недоступна (%s) — rule-based", exc)

    if level == 2 and ca:
        words = re.findall(r"\S+", ca)
        if words:
            return f"Начни так: «{' '.join(words[:7])}…»"

    base = ca or ctx or "материал"
    if level == 1:
        kws = _keywords(base)
        if kws:
            return f"Подумай, что связывает: {', '.join(kws)}."
    # последний fallback
    return "Перечитай материал по теме и попробуй ещё раз — ответ рядом."


def subtask_step(queue: List[str], index: int) -> str:
    """Текущий шаг декомпозиции (клампинг индекса)."""
    if not queue:
        return ""
    return queue[max(0, min(index, len(queue) - 1))]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_scaffold.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/scaffold.py tests/test_scaffold.py
git commit -m "feat(scaffold): лестница подсказок (rule-based + LLM-опция)"
```

### Task B5: Retry/hint-ladder в оценке + маршрут (evaluation.py + graph.py)

**Files:**
- Modify: `src/evaluation.py:61-186` (`evaluate_and_record` — переработать финализацию), `src/graph.py:1726-1733` (`route_tutor`), `src/graph.py:1805-1814` (`evaluate_answer_node` — не менять), `src/graph.py:2036-2037` (рёбра), `src/graph.py:1934-1938` (регистрация узла)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `scaffold.hint_for`, `TutorState.hint_level/retry_question_id/attempts_on_question`, `QuizCard.hint`, `deps.settings.ENABLE_SCAFFOLDING/MAX_HINTS_PER_QUESTION`.
- Produces: WS `quiz.hint` `{question_id, hint, level, attempts_left, subtask: bool}`; `route_tutor` — ожидание ответа при активном вопросе (END вместо регенерации).

- [ ] **Step 1: Write failing tests**

В `tests/test_graph.py`:

```python
def test_hint_ladder_on_wrong_answer(make_settings):
    from src.states import TutorState
    s = make_settings()
    store = NumpyVectorStore("t", FakeEmbedder())
    store.add([DocChunk(text="Атмосфера — газовая оболочка Земли. Азот 78%, кислород 21%.",
                         metadata={"subject": "география", "grade": "6", "topic": "Атмосфера"})])
    events = []
    deps = GraphDeps(embedder=FakeEmbedder(), store=store, settings=s,
                     tutor_llm=lambda m: _GEN, expert_llm=lambda m: _EXPL,
                     on_event=lambda ev, data: events.append((ev, data)))
    g = build_graph(deps)
    st = TutorState(subject="география", topic="Атмосфера", mode="quiz",
                    num_questions=1, source_status="ready")
    st = TutorState(**g.invoke(st.model_dump()))
    st = TutorState(**g.invoke(st.model_dump()))
    card = st.current_question
    # неправильный ответ (закрытый вопрос: выберем заведомо неверный вариант)
    wrong = "неверный вариант"
    st = TutorState(**g.invoke({**st.model_dump(), "pending_answer": wrong}))
    hint_events = [d for ev, d in events if ev == "quiz.hint"]
    assert len(hint_events) == 1
    assert hint_events[0]["question_id"] == card.question_id
    assert hint_events[0]["level"] == 1
    # вопрос НЕ сброшен — ожидаем повторный ответ
    assert st.current_question is not None
    assert st.hint_level == 1
    assert st.answered_count == 0  # не финализирован
```

Проверить: закрытый вопрос из `_GEN` имеет options; выбрать вариант, отсутствующий в эталонах. Если `_GEN` генерирует open-вопрос — ошибочный ответ = "не знаю" (пре-чек завалится → rule-based оценка wrong). Тогда тест адаптировать: `wrong = "не знаю"`.

```python
def test_second_wrong_after_hints_finalizes(make_settings):
    # после 2-х ошибочных попыток (подсказка на 1-й, подсказка на 2-й) — 3-я финализирует
    ... (тот же setup, 3 инвока с неверными ответами)
    assert st.answered_count == 1
    assert st.hint_level == 0  # сброшен после финализации
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph.py::test_hint_ladder_on_wrong_answer -q`
Expected: FAIL — `quiz.hint` не эмитится, `answered_count` уже 1.

- [ ] **Step 3: Implement**

В `src/evaluation.py` переработать хвост `evaluate_and_record` (строки ~128-184). Заменить логику финализации на:

```python
    enable_scaffolding = bool(getattr(getattr(deps, "settings", None), "ENABLE_SCAFFOLDING", True))
    max_hints = int(getattr(getattr(deps, "settings", None), "MAX_HINTS_PER_QUESTION", 2))

    def _finalize(correct_final: bool) -> Tuple[TutorState, str, Optional[Any], Optional[Dict[str, Any]]]:
        """Финализация вопроса: учёт в knowledge_map/bandit/счётчиках, wiki, KG, quiz_complete."""
        nonlocal st
        if correct_final:
            final_score = graded.score if not graded.correct else graded.score
        else:
            final_score = graded.score
        tutor_mod.update_knowledge_map(st, topic, final_score)
        if st.bandit is not None:
            features = adaptive.bandit_features(st)
            adaptive.update_counters(st, graded.correct)
            played = adaptive.difficulty_arm(card.difficulty)
            st.bandit = adaptive.bandit_update(st.bandit, features, played, final_score)
            st.difficulty = adaptive.arm_difficulty(adaptive.bandit_select(st.bandit, features, current_idx=played))
        else:
            tutor_mod.adjust_difficulty(st, graded.correct)

        # Судья (только финальный ответ, открытый вопрос)
        deterministic = graded.model_used == "reference"
        if not deterministic:
            judge_result = judge_evaluation(
                card.question, answer,
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
                card.question, answer, context, st,
                llm_call=getattr(deps, "expert_llm", None), on_token=deps.on_token,
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

        if emit is not None:
            emit("tutor.explanation" if not graded.correct else "system",
                 message=message,
                 citation=(explanation.get("citation") if explanation else None) if not graded.correct else None,
                 correct_count=st.correct_count, answered_count=st.answered_count)

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
                    src = _topic_source(deps.store, card.topic, st)
                    if src:
                        wiki.set_source(st, card.topic, src)
        except Exception as exc:
            logger.warning("Knowledge Wiki per-answer update failed: %s", exc)

        if st.answered_count >= st.num_questions:
            st.quiz_complete = True
            st.session_status = "completed"
        return st, message, judge_result, explanation

    # --- Scaffolding: лестница подсказок ---
    retry = st.retry_question_id == (card.question_id if card else None)
    if enable_scaffolding and card is not None and not graded.correct:
        if not retry and st.hint_level == 0:
            # Первая ошибка: не финализируем, даём подсказку уровня 1
            st.hint_level = 1
            st.attempts_on_question = 1
            st.retry_question_id = card.question_id
            st.pending_answer = None
            hint = card.hint or scaffold.hint_for(card.question, ", ".join(st.current_answers), context, 1)
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
            hint = scaffold.hint_for(card.question, ", ".join(st.current_answers), context, st.hint_level)
            st.agent_message = f"Пока неверно. Подсказка ({st.hint_level}/{max_hints}): {hint}"
            if emit is not None:
                emit("quiz.hint", question_id=card.question_id, hint=hint, level=st.hint_level,
                     attempts_left=max_hints - st.hint_level, subtask=False)
            return st, st.agent_message, None, None

    return _finalize(graded.correct)
```

Важно: удалить прежний дублирующий блок финализации (строки ~101-184) и заменить на `_finalize` + scaffolding-ветки. Импортировать `from . import scaffold as scaffold_mod` (или локально `from .scaffold import hint_for as _scaffold_hint`). Проверить, что `adaptive`, `judge_evaluation`, `tutor_mod`, `_topic_source` уже импортированы в файле.

В `src/graph.py` изменить `route_tutor`:

```python
def route_tutor(state: TutorState) -> str:
    if state.quiz_complete or state.session_status == "failed":
        return NODE_SUMMARY
    if state.subtask_queue is not None:
        return NODE_SUBTASK
    if state.pending_answer is not None:
        return NODE_EVALUATE_ANSWER
    if state.current_question is None:
        return NODE_GENERATE_QUESTION
    return END  # активный вопрос ждёт ответа (в т.ч. после подсказки)
```

Зарегистрировать константу `NODE_SUBTASK = "subtask"` рядом с другими (строки 50-64) и узел `subtask_node` (см. Task B6) в регистрации (строки 1934-1938) — до этого Task B6 узел можно регистрировать пустой заглушкой, но лучше в одном коммите с Task B6. Внимание: если `NODE_SUBTASK` не зарегистрирован, `build_graph` упадёт. Поэтому Task B5 и B6 объединить по регистрации узла — см. Task B6 (шаг 3 добавляет и узел, и рёбра).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evaluation.py src/graph.py tests/test_graph.py
git commit -m "feat(evaluation): лестница подсказок при ошибке (quiz.hint, отложенная финализация)"
```

### Task B6: Декомпозиция — узел subtask_node

**Files:**
- Modify: `src/graph.py` (константа `NODE_SUBTASK`, узел `subtask_node`, регистрация узла, рёбра)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `TutorState.subtask_queue/subtask_index/current_question`, `QuizCard.subtasks`.
- Produces: WS `quiz.hint` с `subtask=True` (пошаговый разбор); после очереди — повторный `quiz.card` исходного вопроса (новая запись `question_id + "-b"`).

- [ ] **Step 1: Write failing test**

В `tests/test_graph.py`:

```python
def test_decomposition_walks_subtasks_then_reasks(make_settings):
    from src.states import TutorState
    s = make_settings()
    store = NumpyVectorStore("t", FakeEmbedder())
    store.add([DocChunk(text="Алгоритм поиска максимума: начать с первого элемента, сравнивать, обновлять.",
                         metadata={"subject": "информатика", "grade": "7", "topic": "Алгоритмы"})])
    events = []
    deps = GraphDeps(embedder=FakeEmbedder(), store=store, settings=s,
                     tutor_llm=lambda m: _GEN_WITH_SUBTASKS, expert_llm=lambda m: _EXPL,
                     on_event=lambda ev, data: events.append((ev, data)))
    g = build_graph(deps)
    st = TutorState(subject="информатика", topic="Алгоритмы", mode="quiz",
                    num_questions=1, source_status="ready")
    st = TutorState(**g.invoke(st.model_dump()))
    st = TutorState(**g.invoke(st.model_dump()))
    card = st.current_question
    assert card.subtasks, "мок должен дать subtasks"
    # исчерпываем подсказки неверными ответами → запускается декомпозиция
    for _ in range(3):
        st = TutorState(**g.invoke({**st.model_dump(), "pending_answer": "не знаю"}))
    assert st.subtask_queue is not None
    # идём по шагам (непустые ответы) → возврат к вопросу
    for _ in range(len(card.subtasks)):
        st = TutorState(**g.invoke({**st.model_dump(), "pending_answer": "понял"}))
    assert st.subtask_queue is None
    assert st.current_question is not None  # исходный вопрос пере-задан
```

Где `_GEN_WITH_SUBTASKS` — JSON-константа с полем `subtasks` (см. Task B3) для темы «Алгоритмы».

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph.py::test_decomposition_walks_subtasks_then_reasks -q`
Expected: FAIL — `subtask_queue` не устанавливается / узел не зарегистрирован.

- [ ] **Step 3: Implement**

В `src/graph.py`:

1. Константа: `NODE_SUBTASK = "subtask"` (в блоке строк 50-64).
2. Функция узла (после `evaluate_answer_node`):

```python
def subtask_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
    """Декомпозиция: пошаговый разбор исходного вопроса (подзадачи → возврат к вопросу)."""
    st = state.model_copy(deep=True)
    if not st.subtask_queue:
        return st.model_dump()

    if st.pending_answer is not None:
        raw = (st.pending_answer or "").strip()
        st.pending_answer = None
        ok = len(raw) >= 3
        if not ok:
            cur = st.subtask_queue[max(0, min(st.subtask_index, len(st.subtask_queue) - 1))]
            st.agent_question = f"Коротко ответь на шаг: {cur}"
            return st.model_dump()
        st.subtask_index += 1
        st.subtask_answer_ok = ok

    if st.subtask_index >= len(st.subtask_queue):
        # Очередь исчерпана → возвращаем исходный вопрос (новая попытка)
        card = st.current_question
        st.subtask_queue = None
        st.subtask_index = 0
        st.hint_level = 0
        st.attempts_on_question = 0
        st.retry_question_id = None
        if card is not None:
            new_qid = f"{card.question_id}-b"
            new_card = card.model_copy(update={"question_id": new_qid})
            st.current_question = new_card
            st.current_answers = list(getattr(st, "current_answers", []) or [])
            st.agent_question = new_card.question
            st.agent_options = new_card.options
            st.records.append({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "question_id": new_qid, "question": new_card.question,
                "options": new_card.options, "answer_type": new_card.answer_type,
                "difficulty": new_card.difficulty, "topic": new_card.topic,
                "section": st.current_section, "student_answer": None,
                "score01": None, "correct": None, "feedback": None,
                "correct_answer": ", ".join(st.current_answers) or None,
                "model_used": None, "judge_score": None,
            })
            _emit(deps, "quiz.card", question_id=new_qid, question=new_card.question,
                  options=new_card.options, answer_type=new_card.answer_type,
                  difficulty=new_card.difficulty, topic=new_card.topic,
                  num_questions=st.num_questions, question_num=st.answered_count + 1,
                  review=False)
        return st.model_dump()

    # Эмитим следующий шаг
    cur = st.subtask_queue[st.subtask_index]
    st.agent_question = cur
    _emit(deps, "quiz.hint", question_id=getattr(st.current_question, "question_id", ""),
          hint=cur, level=st.hint_level, attempts_left=0, subtask=True,
          subtask_index=st.subtask_index + 1, subtask_total=len(st.subtask_queue))
    return st.model_dump()
```

Добавить поле `subtask_answer_ok: bool = False` в `TutorState` (Task B2) — если не добавлено, добавить.

3. Регистрация узла в `build_graph` (строки 1934-1938): добавить `g.add_node(NODE_SUBTASK, _logged_node(deps, NODE_SUBTASK, subtask_node))` — но узлы добавляются как `g.add_node(NODE_TUTOR_NEXT, _logged_node(...))`? Уточнить по факту: регистрация узлов в `build_graph` — см. существующий паттерн (строки 1934-1938). Добавить аналогично.

4. Рёбра: `g.add_edge(NODE_SUBTASK, END)` рядом с `g.add_edge(NODE_GENERATE_QUESTION, END)` (строка 2036).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/graph.py src/states.py tests/test_graph.py
git commit -m "feat(graph): декомпозиция задачи на подзадачи (subtask_node, возврат к исходному вопросу)"
```

### Task B7: Агентные инструменты give_hint + политика scaffolding в промпте

**Files:**
- Modify: `src/agent_tools.py` (инструмент `give_hint`, регистрация, схема), `src/agent_loop.py:300-336` (промпт + `_tutor_context`)
- Test: `tests/test_agent_tools.py`

**Interfaces:**
- Consumes: `scaffold.hint_for`.
- Produces: инструмент `give_hint(args, ctx)`; в `TUTOR_TOOL_NAMES` добавить `"give_hint"`; в `TOOL_SCHEMAS` — запись; в `TUTOR_AGENT_PROMPT` — правило «при ошибке сперва дай подсказку».

- [ ] **Step 1: Write failing test**

В `tests/test_agent_tools.py` (по стилю `_ctx(st)` из файла):

```python
def test_give_hint_tool():
    from src.states import TutorState
    st = TutorState(subject="t", topic="Тема", mode="quiz",
                    current_question=QuizCard(question_id="q1", question="Вопрос?", options=None,
                                              answer_type="open", difficulty="medium", topic="Тема"),
                    current_answers=["ключевой правильный ответ"])
    res, out = execute_agent_tool("give_hint", {"question": "Вопрос?"}, _ctx(st))
    assert '"ok": true' in res or res.startswith('{"ok"')
    assert "подсказ" in res.lower() or "hint" in res.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_agent_tools.py::test_give_hint_tool -q`
Expected: FAIL — `Неизвестный инструмент: give_hint`.

- [ ] **Step 3: Implement**

`src/agent_tools.py` — добавить функцию (после `explain_error`):

```python
def give_hint(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Подсказка по текущему вопросу (уровень 1 или 2), без раскрытия полного ответа."""
    from .scaffold import hint_for

    st = ctx.state
    card = st.current_question
    if card is None:
        return _err("Нет активного вопроса"), st
    level = int(args.get("level") or (st.hint_level + 1))
    hint = hint_for(card.question, ", ".join(st.current_answers),
                    [r.chunk.text for r in _rag_results(ctx, card.question, k=3)], level)
    st = st.model_copy(update={"hint_level": level, "retry_question_id": card.question_id})
    return _ok(hint=hint, level=level, question_id=card.question_id), st
```

Зарегистрировать в `AGENT_TOOLS`: `"give_hint": give_hint,`. В `TOOL_SCHEMAS` добавить:

```python
    {"type": "function", "function": {
        "name": "give_hint",
        "description": "Дать наводящую подсказку по текущему вопросу (не раскрывать полный ответ).",
        "parameters": {"type": "object",
                      "properties": {"level": _param(type="integer", description="1 — наводящая, 2 — раскрывающая")},
                      "required": []}}},
```

`src/agent_loop.py`: в `TUTOR_TOOL_NAMES` добавить `"give_hint"`. В `TUTOR_AGENT_PROMPT` добавить правило:

```python
    "10. При ошибке ученика (evaluate_answer вернул correct=false) НЕ выдавай сразу "
    "правильный ответ: вызови give_hint (уровень 1), затем (при повторной ошибке) give_hint "
    "(уровень 2) или explain_error. Один вопрос — не более двух подсказок."
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_agent_tools.py tests/test_agent_loop.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_tools.py src/agent_loop.py tests/test_agent_tools.py
git commit -m "feat(agent): инструмент give_hint + политика scaffolding в промпте тьютора"
```

**Phase B complete.** Прогнать весь набор: `pytest -q`.

---

## Phase C — Spaced Repetition (SM-2 Question Bank)

### Task C1: Модуль `src/review.py` — SM-2 карточки

**Files:**
- Create: `src/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `Settings.REVIEW_BANK_DIR/REVIEW_QUIZ_SIZE/REVIEW_BANK_MAX_CARDS`.
- Produces:
  - `ReviewCard(BaseModel)` (поля из спеки п.3.3).
  - `ReviewBank(root_dir, student_id, max_cards=200)` с методами:
    - `add_from_record(record: Dict[str, Any]) -> bool`
    - `get_due(subject=None, limit=5) -> List[ReviewCard]`
    - `review_card(card_id: str, correct: bool) -> Optional[ReviewCard]`
    - `stats() -> Dict[str, Any]`
    - `get(card_id) -> Optional[ReviewCard]`
    - `to_dicts() -> List[Dict[str, Any]]`
  - `card_id_for(question: str) -> str` (sha256[:16]).

- [ ] **Step 1: Write failing tests**

`tests/test_review.py`:

```python
import json
from pathlib import Path
from datetime import datetime, timedelta
from src.review import ReviewBank, ReviewCard, card_id_for

def _rec(question="Что такое атмосфера?", correct=False):
    return {"question": question, "options": None, "answer_type": "open",
            "difficulty": "medium", "topic": "Атмосфера", "subject": "география",
            "correct_answer": "Газовая оболочка Земли", "correct": correct,
            "score01": 0.0 if not correct else 1.0}

def test_card_id_stable():
    assert card_id_for("вопрос") == card_id_for("вопрос")
    assert len(card_id_for("вопрос")) == 16

def test_add_from_record_and_dedupe(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    assert bank.add_from_record(_rec()) is True
    assert bank.add_from_record(_rec()) is False  # дубль по question
    cards = bank.to_dicts()
    assert len(cards) == 1

def test_get_due_only_due(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    bank.add_from_record(_rec("q1"))
    bank.add_from_record(_rec("q2"))
    c1 = bank.get(card_id_for("q1"))
    bank.review_card(c1.card_id, correct=True)  # due_at смещён на 1 день → не должен быть due
    due = bank.get_due(limit=5)
    assert len(due) == 1
    assert due[0].question == "q2"

def test_sm2_interval_growth(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    bank.add_from_record(_rec("q"))
    c = bank.get(card_id_for("q"))
    c = bank.review_card(c.card_id, correct=True)
    assert c.interval_days == 1.0
    assert c.reps == 1
    c = bank.review_card(c.card_id, correct=True)
    assert c.interval_days > 1.0
    assert c.reps == 2

def test_sm2_lapse_resets(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    bank.add_from_record(_rec("q"))
    c = bank.get(card_id_for("q"))
    for _ in range(3):
        c = bank.review_card(c.card_id, correct=True)
    assert c.reps == 3
    c = bank.review_card(c.card_id, correct=False)
    assert c.reps == 0
    assert c.interval_days == 1.0
    assert c.lapses == 1
    assert c.ease < 2.5

def test_stats(tmp_path):
    bank = ReviewBank(tmp_path, "s1")
    bank.add_from_record(_rec("q1"))
    bank.add_from_record(_rec("q2", correct=False))
    s = bank.stats()
    assert s["total"] == 2
    assert "due" in s and "by_topic" in s

def test_corrupt_bank_fails_soft(tmp_path):
    p = tmp_path / "s1.json"
    p.write_text("not json", encoding="utf-8")
    bank = ReviewBank(tmp_path, "s1")
    assert bank.get_due() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_review.py -q`
Expected: FAIL — `ModuleNotFoundError: src.review`.

- [ ] **Step 3: Implement**

`src/review.py`:

```python
"""EduTutor — интервальное повторение (SM-2 Question Bank, roadmap).

Карточки ошибочных вопросов копятся в `data/review_bank/<student_id>.json`.
При верном повторе интервал растёт (репетиции → дни); при срыве — интервал сбрасывается.
Дедуп по хэшу текста вопроса; битый файл банка → пустой банк (fail-soft).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("edututor.review")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _parse_iso(value: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def card_id_for(question: str) -> str:
    """Стабильный ID карточки по тексту вопроса."""
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()[:16]


class ReviewCard(BaseModel):
    card_id: str
    student_id: str
    subject: str = ""
    topic: str = ""
    question: str
    options: Optional[List[str]] = None
    answer_type: str = "open"
    correct_answer: str = ""
    difficulty: str = "medium"
    added_at: str = ""
    last_reviewed: str = ""
    due_at: str = ""
    interval_days: float = 1.0
    ease: float = 2.5
    reps: int = 0
    lapses: int = 0

    @property
    def is_due(self) -> bool:
        if not self.due_at:
            return True
        due = _parse_iso(self.due_at)
        return due is None or due <= datetime.datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ReviewBank:
    """Пер-студентный банк карточек (JSON по файлу)."""

    def __init__(self, root_dir: Any, student_id: str, max_cards: int = 200) -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.student_id = student_id
        self.max_cards = int(max_cards or 200)

    def _path(self) -> Path:
        return self.root / f"{self.student_id}.json"

    def _load(self) -> List[ReviewCard]:
        p = self._path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return [ReviewCard.model_validate(x) for x in data if isinstance(x, dict)]
        except Exception as exc:
            logger.warning("ReviewBank %s: битый файл (%s) — пустой банк", self.student_id, exc)
            return []

    def _save(self, cards: List[ReviewCard]) -> None:
        p = self._path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([c.to_dict() for c in cards], ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)

    def get(self, card_id: str) -> Optional[ReviewCard]:
        return next((c for c in self._load() if c.card_id == card_id), None)

    def add_from_record(self, record: Dict[str, Any]) -> bool:
        """Добавить/освежить карточку по записи ошибочного вопроса. True — добавлена."""
        question = str(record.get("question") or "").strip()
        if not question:
            return False
        cid = card_id_for(question)
        cards = self._load()
        existing = next((c for c in cards if c.card_id == cid), None)
        now = _now_iso()
        if existing is None:
            cards.append(ReviewCard(
                card_id=cid, student_id=self.student_id,
                subject=str(record.get("subject") or ""), topic=str(record.get("topic") or ""),
                question=question, options=record.get("options"),
                answer_type=str(record.get("answer_type") or "open"),
                correct_answer=str(record.get("correct_answer") or ""),
                difficulty=str(record.get("difficulty") or "medium"),
                added_at=now, last_reviewed="", due_at=now, interval_days=1.0, ease=2.5,
            ))
        else:
            existing.topic = str(record.get("topic") or existing.topic)
            existing.subject = str(record.get("subject") or existing.subject)
            existing.correct_answer = str(record.get("correct_answer") or existing.correct_answer)
            existing.last_reviewed = ""
        cards = cards[-self.max_cards:]
        self._save(cards)
        return existing is None

    def get_due(self, subject: Optional[str] = None, limit: int = 5) -> List[ReviewCard]:
        cards = self._load()
        due = [c for c in cards if c.is_due and (not subject or c.subject.lower() == subject.lower())]
        due.sort(key=lambda c: _parse_iso(c.due_at) or datetime.datetime.min)
        return due[: int(limit or 5)]

    def review_card(self, card_id: str, correct: bool) -> Optional[ReviewCard]:
        cards = self._load()
        c = next((x for x in cards if x.card_id == card_id), None)
        if c is None:
            return None
        now = datetime.datetime.now()
        if correct:
            c.reps += 1
            if c.reps == 1:
                c.interval_days = 1.0
            else:
                c.interval_days = round(c.interval_days * c.ease, 1)
            c.ease = max(1.3, round(c.ease + (0.1 - max(0, 3 - c.reps) * 0.05), 2))
        else:
            c.reps = 0
            c.interval_days = 1.0
            c.lapses += 1
            c.ease = max(1.3, round(c.ease - 0.2, 2))
        c.last_reviewed = now.isoformat(timespec="seconds")
        c.due_at = (now + datetime.timedelta(days=c.interval_days)).isoformat(timespec="seconds")
        self._save(cards)
        return c

    def stats(self) -> Dict[str, Any]:
        cards = self._load()
        due = [c for c in cards if c.is_due]
        by_topic: Dict[str, int] = {}
        for c in cards:
            by_topic[c.topic] = by_topic.get(c.topic, 0) + 1
        return {
            "total": len(cards),
            "due": len(due),
            "lapses": sum(c.lapses for c in cards),
            "by_topic": by_topic,
        }

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self._load()]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_review.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/review.py tests/test_review.py
git commit -m "feat(review): SM-2 Question Bank (карточки, интервалы, lapse-сброс, дедуп)"
```

### Task C2: Запись карточек при ошибке (evaluation.py)

**Files:**
- Modify: `src/evaluation.py` (`evaluate_and_record` — в `_finalize`, при `not graded.correct`), `src/graph.py` (хелпер `_review_bank`)
- Test: `tests/test_review.py` (интеграционный через graph — в `tests/test_graph.py`)

**Interfaces:**
- Consumes: `ReviewBank`, `Settings.REVIEW_BANK_DIR/ENABLE_SPACED_REPETITION`.
- Produces: хелпер `_review_bank(deps, st) -> Optional[ReviewBank]`; карточки пишутся при неверном ответе.

- [ ] **Step 1: Write failing test**

В `tests/test_graph.py`:

```python
def test_wrong_answer_writes_review_card(make_settings, tmp_path):
    from src.states import TutorState
    s = make_settings()
    s = s.model_copy(update={"REVIEW_BANK_DIR": tmp_path / "review_bank"})
    store = NumpyVectorStore("t", FakeEmbedder())
    store.add([DocChunk(text="Атмосфера — газовая оболочка Земли. Азот 78%, кислород 21%.",
                         metadata={"subject": "география", "grade": "6", "topic": "Атмосфера"})])
    deps = GraphDeps(embedder=FakeEmbedder(), store=store, settings=s,
                     tutor_llm=lambda m: _GEN, expert_llm=lambda m: _EXPL)
    g = build_graph(deps)
    st = TutorState(student_id="stu_x", subject="география", topic="Атмосфера",
                    mode="quiz", num_questions=1, source_status="ready")
    st = TutorState(**g.invoke(st.model_dump()))
    st = TutorState(**g.invoke(st.model_dump()))
    st = TutorState(**g.invoke({**st.model_dump(), "pending_answer": "неверный ответ"}))
    # после подсказки даём ещё один неверный → финализация → карточка
    st = TutorState(**g.invoke({**st.model_dump(), "pending_answer": "неверный ответ"}))
    st = TutorState(**g.invoke({**st.model_dump(), "pending_answer": "неверный ответ"}))
    from src.review import ReviewBank
    bank = ReviewBank(tmp_path / "review_bank", "stu_x")
    assert bank.stats()["total"] >= 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph.py::test_wrong_answer_writes_review_card -q`
Expected: FAIL — `total == 0`.

- [ ] **Step 3: Implement**

В `src/graph.py` добавить хелпер (рядом с `_student_kg`):

```python
def _review_bank(deps: GraphDeps, st: TutorState):
    """ReviewBank для студента (fail-soft: None при выключенной фиче/ошибке)."""
    try:
        if not getattr(getattr(deps, "settings", None), "ENABLE_SPACED_REPETITION", True):
            return None
        if not getattr(st, "student_id", None):
            return None
        from .review import ReviewBank
        return ReviewBank(deps.settings.REVIEW_BANK_DIR, st.student_id,
                          max_cards=getattr(deps.settings, "REVIEW_BANK_MAX_CARDS", 200))
    except Exception as exc:
        logger.warning("ReviewBank unavailable: %s", exc)
        return None
```

В `src/evaluation.py`, в `_finalize`, после записи records (перед блоком Wiki или после — не важно), добавить:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph.py tests/test_review.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/graph.py src/evaluation.py tests/test_graph.py
git commit -m "feat(evaluation): ошибочные вопросы → карточки SM-2 Question Bank"
```

### Task C3: Блиц-опрос в графе (generate/evaluate рельсы)

**Files:**
- Modify: `src/graph.py` (`generate_question_node` — review-ветка; `evaluate_answer_node` — не менять, логика в evaluation), `src/evaluation.py` (review-учёт в начале `evaluate_and_record`)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `ReviewBank.get_due/review_card`, `TutorState.review_*`.
- Produces: `quiz.card` с `review=true` (qid `review:<card_id>`); `system` `kind="review.start"/"review.empty"`; `review.done` `{reviewed, correct, lapses}`; SM-2 применяется при оценке.

- [ ] **Step 1: Write failing test**

В `tests/test_graph.py`:

```python
def test_review_quiz_flow(make_settings, tmp_path):
    from src.review import ReviewBank
    from src.states import TutorState
    s = make_settings()
    s = s.model_copy(update={"REVIEW_BANK_DIR": tmp_path / "review_bank"})
    bank = ReviewBank(tmp_path / "review_bank", "stu_r")
    bank.add_from_record({"question": "Что такое атмосфера?", "options": ["Газовая оболочка Земли", "Океан"],
                          "answer_type": "single", "difficulty": "easy", "topic": "Атмосфера",
                          "subject": "география", "correct_answer": "Газовая оболочка Земли",
                          "correct": False, "score01": 0.0})
    store = NumpyVectorStore("t", FakeEmbedder())
    store.add([DocChunk(text="Атмосфера — газовая оболочка Земли.",
                         metadata={"subject": "география", "grade": "6", "topic": "Атмосфера"})])
    events = []
    deps = GraphDeps(embedder=FakeEmbedder(), store=store, settings=s,
                     tutor_llm=lambda m: _GEN, expert_llm=lambda m: _EXPL,
                     on_event=lambda ev, data: events.append((ev, data)))
    g = build_graph(deps)
    st = TutorState(student_id="stu_r", subject="география", topic="Атмосфера",
                    mode="quiz", num_questions=1, source_status="ready",
                    review_requested=True)
    st = TutorState(**g.invoke(st.model_dump()))
    st = TutorState(**g.invoke(st.model_dump()))
    # первая карточка показана с review=true
    card_events = [d for ev, d in events if ev == "quiz.card" and d.get("review")]
    assert card_events, "должна показаться review-карточка"
    st = TutorState(**g.invoke({**st.model_dump(), "pending_answer": "Газовая оболочка Земли"}))
    done = [d for ev, d in events if ev == "review.done"]
    assert done
    updated = bank.get("01" + "0" * 0)  # проверим через stats
    assert bank.stats()["total"] == 1
```

Примечание: если после review.done граф сразу генерирует обычный вопрос (num_questions=1 уже пройден → summary) — проверить `session_status`. Тест ассертит только `review.done` и наличие карточки в банке.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph.py::test_review_quiz_flow -q`
Expected: FAIL — review-карточка не показывается (`review=true` события нет).

- [ ] **Step 3: Implement**

В `src/graph.py` в `generate_question_node`, в самом начале (до генерации):

```python
    from .graph_helpers import _review_bank  # или локальный import из текущего модуля
```
(хелпер `_review_bank` уже в этом файле — Task C2.)

```python
    review_bank = _review_bank(deps, st)
    if review_bank is not None:
        if st.review_requested and not st.review_active:
            due = review_bank.get_due(subject=getattr(st, "subject", None) or None,
                                      limit=getattr(deps.settings, "REVIEW_QUIZ_SIZE", 5))
            if due:
                st.review_active = True
                st.review_cards = [c.to_dict() for c in due]
                st.review_index = 0
                st.review_reviewed = 0
                st.review_correct = 0
                _emit(deps, "system", message=f"Повторяем {len(due)} карточк(и)…", kind="review.start")
            else:
                _emit(deps, "system", message="Карточек на повторение нет.", kind="review.empty")
            st.review_requested = False

        if st.review_active:
            if st.review_index < len(st.review_cards):
                rc = st.review_cards[st.review_index]
                cid = f"review:{rc['card_id']}"
                card = QuizCard(
                    question_id=cid, question=rc.get("question", ""),
                    options=rc.get("options"), answer_type=rc.get("answer_type", "open"),
                    difficulty=rc.get("difficulty", "medium"), topic=rc.get("topic", ""),
                )
                st.current_question = card
                st.current_answers = [rc.get("correct_answer", "")]
                st.agent_question = card.question
                st.agent_options = card.options
                st.records.append({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "question_id": cid, "question": card.question, "options": card.options,
                    "answer_type": card.answer_type, "difficulty": card.difficulty,
                    "topic": card.topic, "section": st.current_section,
                    "student_answer": None, "score01": None, "correct": None,
                    "feedback": None, "correct_answer": rc.get("correct_answer", ""),
                    "model_used": None, "judge_score": None, "review": True,
                })
                _emit(deps, "quiz.card", question_id=cid, question=card.question,
                      options=card.options, answer_type=card.answer_type,
                      difficulty=card.difficulty, topic=card.topic,
                      num_questions=len(st.review_cards), question_num=st.review_index + 1,
                      review=True)
                return st.model_dump()
            # Очередь исчерпана
            st.review_active = False
            st.review_cards = []
            st.review_index = 0
            _emit(deps, "review.done", reviewed=st.review_reviewed,
                  correct=st.review_correct,
                  lapses=st.review_correct if False else 0)
```

В `src/evaluation.py` в начале `evaluate_and_record` (после `st = st.model_copy(deep=True)` и `topic`) добавить review-учёт:

```python
    is_review = bool(card and str(card.question_id or "").startswith("review:"))
    if is_review:
        context = rag_context(getattr(deps, "store", None), topic, st, k=2)
        if not context:
            context = ["Нет контекста по теме."]
        graded = tutor_mod.evaluate_answer(card.question, answer, context, st,
                                           llm_call=getattr(deps, "eval_llm", None))
        # SM-2
        from .graph import _review_bank
        bank = _review_bank(deps, st)
        card_id = str(card.question_id or "").split(":", 1)[1]
        if bank is not None:
            bank.review_card(card_id, graded.correct)
        # Обновляем Student KG темы (бонус mastery при верном ответе)
        from .evaluation import update_knowledge_map, sync_student_kg
        update_knowledge_map(st, card.topic, graded.score)
        st.review_reviewed += 1
        if graded.correct:
            st.review_correct += 1
        st.review_index += 1
        st.agent_message = f"{'Верно!' if graded.correct else 'Ошибка'} ({round(graded.score * 10, 1)}/10)."
        st.current_question = None
        st.pending_answer = None
        if emit is not None:
            emit("system" if graded.correct else "tutor.explanation",
                 message=st.agent_message,
                 citation=None, correct_count=st.correct_count, answered_count=st.answered_count)
        return st, st.agent_message, None, None
```

Примечание: `update_knowledge_map`/`sync_student_kg` уже определены в `src/evaluation.py` — использовать напрямую, не через импорт самого модуля (уберёте строку импорта `from .evaluation import ...`; просто вызвать локальные функции).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph.py tests/test_review.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/graph.py src/evaluation.py tests/test_graph.py
git commit -m "feat(graph): блиц-опрос по должным карточкам (review=true, SM-2, review.done)"
```

### Task C4: API + CLI + агентные инструменты review

**Files:**
- Create: `api/routes/review.py` (или добавить в `api/routes/students.py` GET и в `api/routes/graph.py` POST)
- Modify: `api/app.py` (подключить роутер, если новый файл), `main.py` (`--review` + пост-квиз промпт), `src/agent_tools.py` (`start_review`, `submit_review`), `src/agent_loop.py` (`TUTOR_TOOL_NAMES` + промпт)
- Test: `tests/test_api.py`, `tests/test_agent_tools.py`

**Interfaces:**
- Consumes: `ReviewBank`, `SessionStore`.
- Produces: `GET /api/students/{id}/review` → `{stats, due:[...]}`; `POST /api/sessions/{id}/review` → `{ok, due_count}`; CLI `--review`; инструменты `start_review`, `submit_review`.

- [ ] **Step 1: Write failing tests**

`tests/test_api.py` (по стилю существующих — фикстура `client`/`store`):

```python
def test_student_review_endpoint(client):
    # student_id из createSession; GET /students/{id}/review → 200 + stats
    r = client.post("/api/sessions", json={"student_id": "stu_review"})
    sid = r.json()["session_id"]
    rr = client.get("/api/students/stu_review/review")
    assert rr.status_code == 200
    assert "stats" in rr.json()
    assert "due" in rr.json()
```

`tests/test_agent_tools.py`:

```python
def test_start_review_tool(tmp_path):
    from src.review import ReviewBank
    ReviewBank(tmp_path, "s1").add_from_record({"question": "q?", "options": None,
        "answer_type": "open", "topic": "Тема", "subject": "s", "correct_answer": "a",
        "correct": False, "score01": 0.0})
    from src.states import TutorState
    st = TutorState(student_id="s1", subject="s", topic="Тема", mode="quiz")
    ctx = _ctx(st)
    ctx.deps.settings = type("S", (), {"REVIEW_BANK_DIR": tmp_path,
                                        "REVIEW_QUIZ_SIZE": 5, "ENABLE_SPACED_REPETITION": True})()
    res, out = execute_agent_tool("start_review", {}, ctx)
    assert '"ok": true' in res
    assert out.review_active is True or out.review_cards
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api.py::test_student_review_endpoint tests/test_agent_tools.py::test_start_review_tool -q`
Expected: FAIL — эндпоинта/инструмента нет.

- [ ] **Step 3: Implement**

**GET** в `api/routes/students.py` (после `get_recommendations`):

```python
@router.get("/{student_id}/review")
def student_review(student_id: str, store: SessionStore = Depends(get_store)):
    """SM-2 Question Bank: статистика + должные карточки."""
    from src.review import ReviewBank
    try:
        bank = ReviewBank(store.settings.REVIEW_BANK_DIR, student_id)
    except Exception:
        return {"stats": {"total": 0, "due": 0, "lapses": 0, "by_topic": {}}, "due": []}
    return {"stats": bank.stats(),
            "due": [c.to_dict() for c in bank.get_due(limit=50)]}
```

Проверить наличие `store.settings` — если в `SessionStore` нет `settings`, использовать `default_settings` из `src.config`. (Уточнить при реализации: в `api/engine.py` SessionStore строится с deps; если settings недоступен — `from src.config import settings as default_settings; REVIEW_BANK_DIR = default_settings.REVIEW_BANK_DIR`.)

**POST** в `api/routes/graph.py` (по паттерну `select_topic`, строки 183-253):

```python
@router.post("/{session_id}/review")
def start_review(session_id: str, store: SessionStore = Depends(get_store)):
    """Запустить блиц-опрос по должным карточкам (по запросу)."""
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Сессия не найдена")
    if session.step_active:
        raise HTTPException(409, "Шаг уже выполняется")
    from src.review import ReviewBank
    student_id = getattr(session.state, "student_id", None) or ""
    due_count = 0
    try:
        bank = ReviewBank(store.settings.REVIEW_BANK_DIR, student_id)
        due_count = len(bank.get_due(limit=50))
    except Exception:
        due_count = 0
    session.state.review_requested = True
    session.state.agent_message = None
    session.state.pending_answer = None
    store.save(session)
    asyncio.create_task(store.run_step(session))
    return {"ok": True, "due_count": due_count}
```

(Импортировать `asyncio` и `HTTPException` — проверить, есть ли уже.)

**main.py:** добавить аргумент `--review` (флаг) и после основного цикла — если задан, установить `state.review_requested = True` и прогнать цикл заново до `review.done`. Конкретно: в блоке инициализации `TutorState` добавить `review_requested=args.review`; в конце основного while-цикла (после `quiz_complete`) при `--review` вывести приглашение «Повторить карточки? (да/нет)» и при «да» — сбросить `quiz_complete=False, session_status=None, review_requested=True, answered_count=0, correct_count=0` и продолжить цикл.

**agent_tools.py:**

```python
def start_review(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Начать блиц-опрос по должным карточкам (SM-2)."""
    from .graph import _review_bank
    from .review import ReviewBank

    st = ctx.state
    bank = _review_bank(ctx.deps, st)
    if bank is None:
        return _err("Интервальное повторение выключено или нет student_id"), st
    due = bank.get_due(subject=getattr(st, "subject", None) or None,
                       limit=getattr(ctx.deps.settings, "REVIEW_QUIZ_SIZE", 5))
    if not due:
        return _ok(due_count=0, message="Карточек на повторение нет"), st
    st = st.model_copy(update={
        "review_requested": False, "review_active": True,
        "review_cards": [c.to_dict() for c in due], "review_index": 0,
        "review_reviewed": 0, "review_correct": 0,
    })
    return _ok(due_count=len(due), cards=[c.question for c in due]), st


def submit_review(args: Dict[str, Any], ctx: AgentToolContext) -> Tuple[str, TutorState]:
    """Оценить ответ по карточке и применить SM-2."""
    from .graph import _review_bank

    st = ctx.state
    card_id = str(args.get("card_id") or "")
    answer = str(args.get("answer") or "")
    bank = _review_bank(ctx.deps, st)
    if bank is None or not card_id:
        return _err("Review недоступен"), st
    card = bank.get(card_id)
    if card is None:
        return _err("Карточка не найдена"), st
    from .tutor import evaluate_answer
    graded = evaluate_answer(card.question, answer, [card.correct_answer], st,
                             llm_call=ctx.llm_call)
    bank.review_card(card_id, graded.correct)
    return _ok(card_id=card_id, correct=graded.correct, score=round(graded.score, 4),
               next_due=card.last_reviewed), st
```

Зарегистрировать `"start_review": start_review, "submit_review": submit_review` в `AGENT_TOOLS`; добавить схемы в `TOOL_SCHEMAS`; добавить в `TUTOR_TOOL_NAMES` (`src/agent_loop.py`); в `TUTOR_AGENT_PROMPT` — «для повторения: start_review → вопрос → submit_review».

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_api.py tests/test_agent_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/students.py api/routes/graph.py main.py src/agent_tools.py src/agent_loop.py tests/test_api.py tests/test_agent_tools.py
git commit -m "feat: review-эндпоинты (GET/POST), CLI --review, агентные start_review/submit_review"
```

### Task C5: Минимальный фронтенд (кнопка «Повторить», hint/review в чате)

**Files:**
- Modify: `frontend/src/api.js` (review методы), `frontend/src/components/StudentKGPanel.jsx` (кнопка), `frontend/src/components/QuizCard.jsx` (бейдж «повторение»), `frontend/src/components/ChatStream.jsx` (рендер quiz.hint / review.done), `frontend/src/App.jsx` (handleEvent — quiz.hint, review.done; проброс onStartReview)
- Test: `frontend/src/components/__tests__/` (существующие тесты компонентов — проверить, что не сломались; при наличии паттерна — добавить рендер-тест hint)

**Interfaces:**
- Consumes: WS-события `quiz.hint`, `review.done`, `quiz.card` (review=true); REST `GET /api/students/{id}/review`, `POST /api/sessions/{id}/review`.
- Produces: кнопка «Повторить» в «Моих знаниях» (видна при due>0), hint-карточка в чате, сообщение review.done.

- [ ] **Step 1: Implement (фронтенд без TDD — существующие e2e-тесты не покрывают)**

`frontend/src/api.js` — добавить в объект `api`:

```js
  getReview: (studentId) =>
    jsonFetch(`/api/students/${encodeURIComponent(studentId)}/review`),

  startReview: (id) =>
    jsonFetch(`/api/sessions/${id}/review`, { method: 'POST' }),
```

`frontend/src/components/StudentKGPanel.jsx` — добавить состояние `dueCount` (fetch `getReview`), и в header кнопку:

```jsx
        <div className="student-kg-panel__actions">
          {dueCount > 0 && (
            <button className="btn btn-small" onClick={onStartReview} disabled={busy}>
              Повторить ({dueCount})
            </button>
          )}
        </div>
```

Пропсы компонента расширить: `({ studentId = '', subject = '', onStartReview = null, busy = false })`. Fetch due:

```js
  useEffect(() => {
    if (!studentId) return
    api.getReview(studentId)
      .then((d) => setDueCount(d.stats?.due || 0))
      .catch(() => setDueCount(0))
  }, [studentId])
```

Импортировать `api` из `../api`.

`frontend/src/components/QuizCard.jsx` — в `quiz-meta` добавить бейдж:

```jsx
        {q.review && <span className="badge review">повторение</span>}
```

`frontend/src/components/ChatStream.jsx` — в рендер добавить:

```jsx
          {m.kind === 'hint' && (
            <div className="bubble agent hint">💡 <LatexText text={m.text} /></div>
          )}
          {m.kind === 'review' && <div className="bubble review">🔁 {m.text}</div>}
```

`frontend/src/App.jsx` — в `handleEvent` добавить ветки:

```js
      case 'quiz.hint':
        setFeed((f) => [...f, { id: `hint-${Date.now()}`, kind: 'hint', text: data.hint, data }])
        break
      case 'review.done':
        setFeed((f) => [...f, {
          id: `review-${Date.now()}`, kind: 'review',
          text: `Повторение завершено: верно ${data.correct} из ${data.reviewed}.`,
          data,
        }])
        break
```

В `handleEvent` для `quiz.card` сохранить `data.review` в элементе feed (в объекте current/feed) — проверить текущее место `setCurrent({ kind: 'quiz', ... })` (App.jsx:306-326) и добавить `review: data.review` в сохраняемое.

`onStartReview` в App.jsx: `() => { api.startReview(sessionId).catch(...) }` — пробросить в `StudentKGPanel` через пропс `onStartReview` (найти, где рендерится панель, и передать).

Проверить CSS-классы `bubble hint`/`bubble review` — при необходимости добавить минимальные стили в `frontend/src/index.css` (переиспользовать `.bubble.system`).

- [ ] **Step 2: Run frontend tests/build**

Run: `cd frontend && npm run build` (или `npx vitest run` если настроен).
Expected: сборка без ошибок; существующие компонентные тесты зелёные.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.js frontend/src/components/StudentKGPanel.jsx frontend/src/components/QuizCard.jsx frontend/src/components/ChatStream.jsx frontend/src/App.jsx frontend/src/index.css
git commit -m "feat(frontend): кнопка «Повторить», hint/review в чате, бейдж повторения"
```

**Phase C complete.** Прогнать весь набор: `pytest -q` + фронтенд-сборка.

---

## Финальная проверка

- [ ] `pytest -q` — весь набор зелёный (существующие + новые).
- [ ] `python main.py --scenario schoolchild_grade6_geography --mock --auto --questions 3` — офлайн-прогон без падений.
- [ ] `cd frontend && npm run build` — сборка без ошибок.
- [ ] Обновить `roadmap.md` (пометить выполненные пункты) и README-раздел «Адаптивное обучение».
