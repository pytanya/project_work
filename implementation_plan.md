# Оптимизация стриминга и визуализации EduTutor

Критический анализ проблем с «зависанием» UI при подготовке урока и рекомендации по оптимизации.

## Корневые причины зависания

Из скриншота и анализа кода выявлены **5 критических проблем**:

---

### 🔴 Проблема 1: `selectTopic` → `run_step` — HTTP блокирует до полного завершения

> [!CAUTION]
> Главная причина «зависания»: выбор темы в графе запускает **синхронный HTTP POST** ([`api.selectTopic`](file:///c:/otus/project_work/frontend/src/api.js#L48-L49)), который вызывает [`run_step(session)`](file:///c:/otus/project_work/api/routes/graph.py#L142) и ждёт **полного завершения** графа (LLM-вызовы + RAG + генерация урока/квиза). Это 30–120 сек молчания.

**Что происходит:**
1. Пользователь кликает тему → [`handleSelectTopic`](file:///c:/otus/project_work/frontend/src/App.jsx#L483-L516) отправляет POST
2. Бэкенд: [`select_topic`](file:///c:/otus/project_work/api/routes/graph.py#L102-L157) → [`run_step`](file:///c:/otus/project_work/api/engine.py#L211-L259) → `graph.invoke()` в `asyncio.to_thread`
3. Граф проходит через `content_node` → `generate_text` → LLM стримит токены
4. Токены идут в WS-очередь, **но фронтенд не обрабатывает WS пока ждёт HTTP-ответ**
5. Фронтенд показывает `chatBusy=true` (3 точки), но **нет процентного прогресса**

**Результат:** UI «застыл» на 1-2 минуты. Пользователь не видит, что происходит.

---

### 🔴 Проблема 2: Нет гранулярных событий прогресса при подготовке урока

На скриншоте видно множество `intake`/`status` пар в Network, но при подготовке урока (после выбора темы) нет промежуточных событий. Поток:

```
[клик по теме] → system «Готовимся по теме...» → [ТИШИНА 30-120 сек] → tutor.lesson + intake.question
```

В [`content_node`](file:///c:/otus/project_work/src/graph.py#L501-L562) нет событий `source.progress` между началом и концом генерации. Событие [`tutor.lesson`](file:///c:/otus/project_work/src/graph.py#L557) приходит только после полной генерации.

---

### 🟡 Проблема 3: Стриминг токенов работает, но визуально незаметен

Токены идут через WS (`token` events → [`pushToken`](file:///c:/otus/project_work/frontend/src/App.jsx#L105-L114)), но:
- Пузырь `stream` в [`ChatStream`](file:///c:/otus/project_work/frontend/src/components/ChatStream.jsx#L29-L33) — простой текст без форматирования
- Нет индикатора «генерация урока: 35%» или «подождите, обрабатываю...»
- `chatBusy` показывает только 3 точки (typing indicator) — нет контекста что именно «думает» тьютор

---

### 🟡 Проблема 4: Граф знаний перерисовывается целиком при каждом обновлении

[`KnowledgeGraphPanel`](file:///c:/otus/project_work/frontend/src/components/KnowledgeGraphPanel.jsx) — это SVG, перерисовываемый через `useMemo`. Когда `refreshGraph()` обновляет state → весь компонент перерисовывается. При ~50+ узлах + tooltip + hover-эффекты — это дёргает UI.

Кроме того, `radialLayout()` пересчитывается при каждом изменении `nodes`/`edges` — нет кеширования layout между обновлениями.

---

### 🟡 Проблема 5: Timeout fallback 240 сек — слишком длинный

В [`submitAnswer`](file:///c:/otus/project_work/frontend/src/App.jsx#L399-L407) timeout = **240 секунд**. Если WS-событие не пришло, пользователь ждёт 4 минуты, думая что проект завис.

---

## Proposed Changes

### Компонент 1: Гранулярный прогресс при подготовке урока (Backend)

#### [MODIFY] [graph.py](file:///c:/otus/project_work/src/graph.py)

Добавить промежуточные события прогресса в `content_node`:

```diff
 def content_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
     ...
     # Генерируем материал по активной теме и режиму
+    _emit(deps, "source.progress", stage="content", url="", status="generating",
+          message=f"Ищу материалы по теме «{topic}»…")
     chunks = _rag_chunks(deps.store, topic, st, k=k)
     context = [c.chunk.text for c in chunks] or ["Нет контекста по теме."]
+    _emit(deps, "source.progress", stage="content", url="", status="generating",
+          message=f"Генерирую {_MODE_LABELS.get(mode, 'материал')} по теме «{topic}» ({len(context)} фрагментов)…")
     on_token = deps.on_token
     if mode == "deep_dive":
         st.lesson_text = tutor_mod.generate_deep_dive(...)
```

Аналогично добавить в `generate_question_node` и `agent_tutor_node`:
```diff
 def generate_question_node(state: TutorState, deps: GraphDeps) -> Dict[str, Any]:
     ...
+    _emit(deps, "source.progress", stage="quiz", url="", status="generating",
+          message=f"Генерирую вопрос по теме «{topic}»…")
     card = tutor_mod.generate_question(...)
```

---

### Компонент 2: Разделение selectTopic — fire-and-forget + WS (Backend)

#### [MODIFY] [graph.py (routes)](file:///c:/otus/project_work/api/routes/graph.py)

Переделать `select_topic` endpoint из синхронного `await run_step` на fire-and-forget:

```diff
 @router.post("/topic")
 async def select_topic(session_id: str, body: TopicBody, store: SessionStore = Depends(get_store)):
     session = get_session(store, session_id)
     ...
     session.state = session.state.model_copy(update={...})
-    await run_step(session)
-    return {"ok": True, "active_topic": ..., "question": ..., "lesson": ...}
+    # Fire-and-forget: запускаем шаг графа в фоне, результат придёт через WS
+    import asyncio
+    asyncio.create_task(_run_step_background(session))
+    return {"ok": True, "active_topic": session.state.active_topic, "title": title}
```

Добавить helper:
```python
async def _run_step_background(session: SessionData):
    """Фоновый шаг графа — результат публикуется через WS-очередь."""
    try:
        await run_step(session)
    except Exception as e:
        logger.exception("Background run_step error: %s", e)
        from api.schemas import WsEvent
        session.queue.put(WsEvent(event="session.error", data={"message": str(e)}))
```

---

### Компонент 3: Фронтенд — прогресс-бар и улучшение обратной связи

#### [MODIFY] [App.jsx](file:///c:/otus/project_work/frontend/src/App.jsx)

1. **Новый state для фазы прогресса:**
```diff
+ const [progressPhase, setProgressPhase] = useState(null) // {stage, message}
```

2. **Обработка `source.progress` с фазой:**
```diff
  case 'source.progress':
    endStream()
    setSource({ status: d.status, note: d.message })
+   setProgressPhase({ stage: d.stage, message: d.message, status: d.status })
+   // Автосброс при завершении
+   if (d.status === 'done' || d.status === 'ready') {
+     setTimeout(() => setProgressPhase(null), 1500)
+   }
    ...
```

3. **Обновить `handleSelectTopic` — не блокировать на HTTP:**
```diff
  async function handleSelectTopic(node) {
    setChatBusy(true)
+   setProgressPhase({ stage: 'topic', message: `Готовимся по теме: ${displayTitle}...` })
    push('system', `Готовимся по теме: ${displayTitle}...`)
    try {
      const r = await api.selectTopic(sessionId, node.id)
      setGraph((g) => ({ ...g, activeTopic: r.active_topic }))
-     // Если бэкенд уже сгенерировал вопрос - обновляем UI
-     if (r.question) { ... }
-     else if (r.lesson) { ... }
+     // Вопрос/урок придут через WS — не ждём в HTTP-ответе
    } catch (e) {
      push('error', String(e.message || e))
+     setProgressPhase(null)
    } finally {
-     setChatBusy(false)
+     // chatBusy сбросится при получении WS-события
    }
  }
```

#### [MODIFY] [ChatStream.jsx](file:///c:/otus/project_work/frontend/src/components/ChatStream.jsx)

Заменить typing indicator (3 точки) на контекстный прогресс:

```diff
- {busy && (
-   <div className="msg typing-indicator">
-     <div className="bubble agent typing">
-       <span className="dot" /><span className="dot" /><span className="dot" />
-     </div>
-   </div>
- )}
+ {busy && (
+   <div className="msg progress-indicator">
+     <div className="bubble agent progress">
+       {progressPhase ? (
+         <>
+           <div className="progress-text">{progressPhase.message}</div>
+           <div className="progress-bar-wrap">
+             <div className="progress-bar-indeterminate" />
+           </div>
+         </>
+       ) : (
+         <><span className="dot" /><span className="dot" /><span className="dot" /></>
+       )}
+     </div>
+   </div>
+ )}
```

---

### Компонент 4: Улучшение графа знаний

#### [MODIFY] [KnowledgeGraphPanel.jsx](file:///c:/otus/project_work/frontend/src/components/KnowledgeGraphPanel.jsx)

1. **Мемоизация layout (не пересчитывать при hover/zoom):**
```diff
- const layout = useMemo(() => radialLayout(nodes, edges), [nodes, edges])
+ const layoutKey = useMemo(() => JSON.stringify(nodes.map(n => n.id).sort()), [nodes])
+ const layout = useMemo(() => radialLayout(nodes, edges), [layoutKey, edges])
```

2. **Анимация выбранного узла (пульсация):**
```diff
  <circle r={r} fill={fill}
+   className={active ? 'kg-pulse' : ''}
    opacity={active ? 0.95 : 0.85}
```

3. **Визуальная индикация загрузки при выборе темы:**
```diff
  {busy && activeTopic && (
    <div className="graph-loading-overlay">
      <div className="graph-spinner" />
      <div className="graph-loading-text">Готовим материал…</div>
    </div>
  )}
```

4. **Force-directed layout для лучшей визуализации (вместо radial при >15 узлов):**

При большом числе узлов radialLayout складывает всё в круг, что нечитаемо. Предлагаю простой force-simulation (без d3-force, на чистом JS ~30 строк), который разводит узлы с перекрытием.

---

### Компонент 5: Уменьшение timeout и добавление heartbeat

#### [MODIFY] [App.jsx](file:///c:/otus/project_work/frontend/src/App.jsx)

```diff
- const timeout = setTimeout(() => {
-   if (isWaitingForAnswer.current) {
-     setChatBusy(false)
-     isWaitingForAnswer.current = false
-   }
- }, 240000)
+ // Уменьшаем timeout до 120 сек, но source.progress события сбрасывают таймер
+ const ANSWER_TIMEOUT = 120000
+ let timeout = setTimeout(resetBusy, ANSWER_TIMEOUT)
+ 
+ // Каждое WS-событие progress продлевает таймаут (heartbeat)
```

#### [MODIFY] [engine.py](file:///c:/otus/project_work/api/engine.py)

Добавить heartbeat-события каждые 15 сек при долгом `run_step`:

```diff
 async def run_step(session: SessionData, ...) -> TutorState:
+    # Heartbeat: каждые 15 сек шлём событие, чтобы фронтенд знал что мы живы
+    async def _heartbeat():
+        while session.step_active:
+            await asyncio.sleep(15)
+            if session.step_active:
+                session.queue.put(WsEvent(event="system.heartbeat",
+                    data={"message": "Обработка продолжается…", "elapsed": ...}))
+    hb_task = asyncio.create_task(_heartbeat())
     session.step_active = True
     try:
         session.state = await asyncio.wait_for(...)
     finally:
         session.step_active = False
+        hb_task.cancel()
```

---

### Компонент 6: CSS для прогресса и анимаций

#### [MODIFY] [index.css](file:///c:/otus/project_work/frontend/src/index.css)

Добавить стили для:
- Indeterminate progress bar в чате
- Пульсация активного узла в графе
- Overlay загрузки на панели графа
- Плавные переходы вместо резких появлений

---

## Open Questions

> [!IMPORTANT]
> **Fire-and-forget vs await:** Изменение `select_topic` на fire-and-forget означает, что HTTP-ответ больше не содержит `question`/`lesson`. Фронтенд будет получать их только через WS. Это **breaking change** для flow, где WS не подключен. Считаете ли вы это приемлемым, или нужен fallback через polling?

> [!IMPORTANT]
> **Force-directed layout:** Хотите ли вы перейти на force-directed layout для графа вместо radial, или достаточно оптимизировать текущий radial (он проще, но плохо масштабируется >20 узлов)?

> [!NOTE]
> **Heartbeat interval:** Предлагаю 15 сек, но можно уменьшить до 5 сек для более отзывчивого UX. Какое значение предпочитаете?

---

## Verification Plan

### Automated Tests
```bash
cd c:\otus\project_work
python -m pytest tests/ -x -v
```

### Manual Verification
1. Запустить бэкенд + фронтенд
2. Пройти intake (класс, предмет, тема)
3. Выбрать тему в графе → убедиться что:
   - Прогресс-бар появляется мгновенно
   - WS-события `source.progress` приходят каждые несколько секунд
   - Токены стримятся в реальном времени
   - UI не «застывает»
4. Проверить что граф не дёргается при hover/zoom во время генерации
