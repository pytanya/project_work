# Код-ревью: оптимизация стриминга EduTutor

**Коммит:** `dc468b1` — *perf(streaming): fire-and-forget выбор темы, гранулярный прогресс и heartbeat*
**Тесты:** ✅ 32/32 passed (52.75s)

> [!NOTE]
> Все замечания этого ревью (1–7) закрыты в последующем коммите — *fix(code-review): race conditions,
> force-layout, heartbeat elapsed, _bg_tasks, CSS*. См. «Статус исправлений» внизу файла.

---

## Реализовано из плана

| # | Компонент | Статус | Качество |
|---|-----------|--------|----------|
| 1 | Гранулярный прогресс в `content_node`, `generate_question_node`, `agent_tutor_node` | ✅ | ⭐⭐⭐⭐ |
| 2 | Fire-and-forget `select_topic` | ✅ | ⭐⭐⭐ (есть проблемы) |
| 3 | Контекстный прогресс-бар в `ChatStream` | ✅ | ⭐⭐⭐⭐⭐ |
| 4 | Мемоизация layout + force-directed + пульсация + overlay | ✅ | ⭐⭐⭐⭐ |
| 5 | Heartbeat + уменьшение timeout | ✅ | ⭐⭐⭐⭐ |
| 6 | CSS анимации + `prefers-reduced-motion` | ✅ | ⭐⭐⭐⭐⭐ |

---

## 🔴 Критические проблемы

### 1. Race condition: двойной клик по теме запустит два фоновых `run_step`

В [`select_topic`](file:///c:/otus/project_work/api/routes/graph.py#L127-L181): каждый POST создаёт `asyncio.create_task(_run_step_background(session))`. Если пользователь кликнет по другой теме, пока первая генерация идёт → **два шага графа одновременно мутируют `session.state`**.

```python
# api/routes/graph.py:170
task = asyncio.create_task(_run_step_background(session))
_bg_tasks.add(task)
```

**Нет проверки:** если `session.step_active` уже `True`, запрос должен либо:
- Отклоняться (HTTP 409 Conflict)
- Отменять предыдущий шаг через `session.cancel_event.set()`

**Рекомендуемый фикс:**
```python
# В начало select_topic, перед fire-and-forget:
if session.step_active:
    return {"ok": False, "error": "Идёт подготовка предыдущей темы. Дождитесь завершения."}
```

Или лучше — на фронтенде блокировать клики по темам пока `isPreparingTopic.current`:
```jsx
// KnowledgeGraphPanel.jsx — кнопки тем
disabled={busy}  // ← уже есть, но busy не передаётся как isPreparingTopic
```

> [!CAUTION]
> Без этого фикса два параллельных `_invoke()` → `graph.invoke()` могут сломать состояние сессии (перезапись `session.state` из разных потоков).

---

### 2. `isWaitingForAnswer` и `isPreparingTopic` конфликтуют

В [`handleEvent`](file:///c:/otus/project_work/frontend/src/App.jsx#L150-L179): при `source.progress` событии `isWaitingForAnswer` ветка (строка 156-164) **сбросит `chatBusy=false`** до того, как fire-and-forget завершится:

```javascript
// Строка 156-164: isWaitingForAnswer проверяется ПЕРЕД isPreparingTopic
if (isWaitingForAnswer.current) {
  const answerResolvedEvents = [..., 'source.progress']  // ← !!!
  if (answerResolvedEvents.includes(evt.event)) {
    setChatBusy(false)  // ← сбросит busy!
    isWaitingForAnswer.current = false
  }
}

// Строка 169-178: isPreparingTopic потом НЕ восстановит busy
if (isPreparingTopic.current) {  // ← но busy уже false!
  ...
}
```

**Проблема:** если до fire-and-forget пользователь отправлял ответ (isWaitingForAnswer=true) и затем кликает тему, оба флага могут оказаться true. `source.progress` сбросит busy из-за isWaitingForAnswer, хотя isPreparingTopic всё ещё ждёт.

**Рекомендуемый фикс:** `source.progress` не должен быть в `answerResolvedEvents`:
```diff
 const answerResolvedEvents = [
-  'quiz.card', 'tutor.lesson', 'tutor.explanation', 'system',
-  'tutor.summary', 'intake.question', 'source.progress'
+  'quiz.card', 'tutor.lesson', 'tutor.explanation',
+  'tutor.summary', 'intake.question'
 ]
```

---

## 🟡 Средние проблемы

### 3. Force-directed layout: 260 итераций в `useMemo` — потенциально дорого

В [`forceLayout`](file:///c:/otus/project_work/frontend/src/components/KnowledgeGraphPanel.jsx#L51-L134): цикл `for (let iter = 0; iter < 260; iter++)` с вложенным `O(n²)` отталкиванием. При 100 узлах: 260 × 100² = **2.6M операций** синхронно в рендере.

```javascript
for (let iter = 0; iter < 260; iter++) {    // 260 итераций
  for (let i = 0; i < ids.length; i++) {     // O(n)
    for (let j = i + 1; j < ids.length; j++) { // O(n)
      // отталкивание
```

`useMemo` выполняется синхронно в рендере → при первом появлении графа с 50+ узлов UI **заморозится на 50-200ms**.

**Рекомендация:**
- Уменьшить до 80-120 итераций (обычно хватает для convergence)
- Или вынести в `useEffect` + `requestAnimationFrame` с инкрементальной анимацией

---

### 4. `layoutKey` включает `edges` в зависимости, но `layout` зависит от `nodes` и `edges`

```javascript
const layoutKey = useMemo(
  () => (nodes || []).map((n) => n.id).sort().join('|') + '#' +
        (edges || []).map((e) => `${e.source}->${e.target}`).sort().join('|'),
  [nodes, edges],
)
const layout = useMemo(() => computeLayout(nodes, edges), [layoutKey, nodes, edges])
//                                                         ^^^^^^^^^^^^^^^^^^^^^^^^^^
```

Зависимости `layout` включают и `layoutKey`, и `nodes`, и `edges`. React будет пересчитывать `layout` при **любом** изменении ссылки `nodes`/`edges`, даже если `layoutKey` не изменился. Это сводит на нет мемоизацию.

**Фикс:**
```diff
- const layout = useMemo(() => computeLayout(nodes, edges), [layoutKey, nodes, edges])
+ const layout = useMemo(() => computeLayout(nodes, edges), [layoutKey])
```

> [!WARNING]
> С `[layoutKey]` нужно убедиться, что `nodes` и `edges` ссылки стабильны внутри замыкания. Поскольку `layoutKey` уже зависит от `[nodes, edges]`, это должно быть безопасно — при изменении `nodes`/`edges` обновится и `layoutKey`.

---

### 5. Heartbeat не показывает elapsed в UI

В [`engine.py`](file:///c:/otus/project_work/api/engine.py#L246) heartbeat шлёт `elapsed`:
```python
session.queue.put(WsEvent(
    event="system.heartbeat",
    data={"message": "Обработка продолжается…", "elapsed": elapsed},
))
```

Но в [`App.jsx:244-250`](file:///c:/otus/project_work/frontend/src/App.jsx#L244-L250) `elapsed` никогда не используется:
```javascript
case 'system.heartbeat':
  resetBusyAfterTimeout()
  if (isPreparingTopic.current) {
    setProgressPhase((p) => ({
      stage: p?.stage || 'topic',
      message: d.message || p?.message || 'Обработка продолжается…', // elapsed проигнорирован
    }))
```

Это упущенная возможность: можно показать «Обработка продолжается… (45 сек)».

---

## 🟢 Мелкие замечания

### 6. `_bg_tasks` — глобальный set без ограничения

[`_bg_tasks`](file:///c:/otus/project_work/api/routes/graph.py#L26) — module-level set, куда добавляются задачи. Если сервер перезагрузится (hot reload в dev), старые задачи останутся в памяти. Это не проблема в production (Uvicorn), но в dev-режиме может привести к утечкам.

### 7. CSS: `progress-bar-indeterminate` z-index

Нет `z-index` у `.graph-loading-overlay`. При `position: absolute` с `inset: 0` наложение работает, но `z-index: 5` может конфликтовать с tooltip (`kg-tooltip`), у которого нет явного z-index.

---

## Что сделано хорошо ✅

1. **Fire-and-forget паттерн** — правильно: `asyncio.create_task` + `_bg_tasks` set для защиты от GC. Ошибки корректно ловятся и публикуются как `session.error`.

2. **Heartbeat** — грамотно: `async def _heartbeat()` в `run_step` с `hb_task.cancel()` в `finally`. Не течёт.

3. **Тест обновлён** — `test_select_topic_generates_question` теперь проверяет fire-and-forget: `assert "question" not in r.json()` + WS ожидание финального события. Хорошо.

4. **CSS** — `prefers-reduced-motion: reduce` отключает все анимации для a11y. Прогресс-бар с `animation: progress-slide` визуально понятен.

5. **Force-directed** — гибридный подход (radial ≤20, force >20) — правильное решение. Реализация простая и работает без d3.

6. **Гранулярные события** — добавлены в 3 узла (`content_node`, `generate_question_node`, `agent_tutor_node`). Это именно то, что убирает «тишину».

---

## Итоговая оценка

| Критерий | Оценка |
|----------|--------|
| Решение корневой проблемы (зависание UI) | ⭐⭐⭐⭐⭐ |
| Качество кода | ⭐⭐⭐⭐ |
| Покрытие тестами | ⭐⭐⭐⭐ |
| Потенциальные race conditions | ⭐⭐ (нужен фикс #1, #2) |
| Performance | ⭐⭐⭐ (нужен фикс #3, #4) |
| UX/CSS | ⭐⭐⭐⭐⭐ |

**Общая оценка: 4/5** — отличная реализация плана, нужно закрыть race condition (проблемы #1, #2) и оптимизировать force-layout (#3, #4).

---

## ✅ Статус исправлений (2026-08-20)

| # | Проблема | Статус | Исправление |
|---|----------|--------|-------------|
| 1 | Race condition: двойной клик → два `run_step` | ✅ | `session.step_active` гвард в `select_topic` → **HTTP 409** (`api/routes/graph.py`). Флаг ставится синхронно перед `create_task` (закрыто окно проверка→старт). Фронтенд блокирует клики, пока `isPreparingTopic` (`frontend/src/App.jsx:handleSelectTopic`). Тест: `test_select_topic_rejects_double_click_with_409`. |
| 2 | Конфликт `isWaitingForAnswer`/`isPreparingTopic` | ✅ | `source.progress` исключён из `answerResolvedEvents`; блок сброса busy не срабатывает во время `isPreparingTopic`; `handleSelectTopic` суперсидирует `isWaitingForAnswer` (`frontend/src/App.jsx`). |
| 2a | Индикатор «раздумий» после отправки ответа | ✅ | `system` разделён по `kind`: промежуточные (`intent`, `intake.warning`) НЕ сбрасывают busy, финальные (`topic.all`, `topic.selected`, `lesson.done`, `lesson.repeat`, `lesson.ready`, `agent.message`, `doc.scanned`) — сбрасывают. Добавлены `source.failed`/`session.error` в `answerResolvedEvents` (`frontend/src/App.jsx`). |
| 3 | Force-directed: 260 итераций в `useMemo` | ✅ | Снижено до **120** итераций (`KnowledgeGraphPanel.jsx:forceLayout`) — сходимость сохраняется, UI не замораживается. |
| 4 | `layout` зависел от `[layoutKey, nodes, edges]` | ✅ | `useMemo(() => computeLayout(nodes, edges), [layoutKey])` — мемоизация работает (`KnowledgeGraphPanel.jsx:166`). |
| 5 | Heartbeat `elapsed` не показывается | ✅ | `system.heartbeat` форматирует «Обработка продолжается… (N сек)» (`App.jsx:system.heartbeat`). |
| 6 | `_bg_tasks` без ограничения | ✅ | `add_done_callback(_bg_tasks.discard)` + лимит `_MAX_BG_TASKS=32` (отмена старейшей) (`api/routes/graph.py:_track_background_task`). |
| 7 | CSS z-index/конфликт `.kg-tooltip` | ✅ | Удалён мёртвый блок donut-чарта, перекрывавший `.kg-tooltip` (`width`/`background`); z-index-иерархия overlay (5) < tooltip (30) прокомментирована (`index.css`). |

**Тесты:** backend 35/35 (включая новый 409-гвард), frontend 39/39.
