# Исследование: время жизни сессии и таймауты длинных операций

> Дата: 2026-09-01. Тип: исследование/требования (roadmap #9). Контекст:
> запрос — «как долго живёт сессия? не может быть, что она завершается по
> таймауту в процессе работы». Референсы: HKUDS/DeepTutor, Nutlope/llamatutor,
> LangGraph, платформенные лимиты Vercel/Edge.

## 1. Ключевой вывод

**«Сессия жива» ≠ «транспорт жив» ≠ «работа жива».** В производственных
тьюторах это три раздельные сущности, и умирать может только *транспорт*
(и то переподключаться):

| Сущность | Жизнь |
|---|---|
| **Сессия** (диалог) | durable, в SQLite; переживает рестарты; **без TTL-убийства** (TLT — только для горячего кэша в памяти) |
| **Ход/шаг** (turn) | фоновая задача, **отвязана от WS**; умирает только по явному cancel / stall-детектору / рестарту (→ `interrupted`) |
| **WS** (транспорт) | подписка на события с replay (`after_seq`); heartbeat + auto-reconnect |

### Сравнительная таблица

| Проект | Модель сессии | Таймауты / keep-alive | Гарантия длинной операции |
|---|---|---|---|
| **HKUDS/DeepTutor** (FastAPI+Next.js) | Durable диалог в SQLite (`sessions`+`messages`) + `turns` (`running/completed/failed/cancelled/rejected`) + per-turn event log с `seq`. **Без TTL** — живёт бесконечно | Client WS ping **30s**, dead-detection **45s**, auto-reconnect с exp. backoff + `resume_from(after_seq)`; `wake()` на focus/online. Единственный kill — явный `cancel_turn`. `asyncio.wait_for(20s)` — только для генерации заголовка | Turn = background `asyncio.Task`, **не привязан к WS**; WS — подписчик. Рестарт: orphan-running-turns финализируются `failed` «Turn interrupted by server restart. Please retry» |
| **Nutlope/llamatutor** (Next.js+Edge) | **Нет серверной сессии** вообще (React `useState`; refresh → потеряно) | Нет heartbeat, нет idle. Ограничение — **Vercel max duration: Hobby 300s/5min**, поток обрезается платформой | Нет (анти-пример). «Сессия умирает в процессе работы» — ровно то, что нам не нужно |
| **LangGraph** (фреймворк) | Thread = диалог в checkpointer; время жизни = политика retention бэкенда, не wall-clock | Нет idle-таймаута. HITL = `interrupt()`/`resume(thread_id)` — **тот же thread_id**, таймер не может убить прогон | Durability + resume по thread_id; рекомендуют не автоделить thread, retention — cron-очисткой |
| **Vercel/Edge** | — | Для HTTP/1.1 платформа НЕ шлёт keep-alive; рекомендуют **«stream progress/heartbeat while working»**; Workflows для пауз от минут до месяцев | Потолок serverless — жёсткий kill; долгую операцию надо выносить в durable background worker |

## 2. Рекомендации для EduTutor (требования)

1. **Сессия живёт практически вечно.** In-memory TTL (сейчас 1800s) — это
   «крутилка памяти», а не «уничтожитель диалога». Дефолт: горячий кэш 30–60 мин
   **неактивности** (от последнего события, не от создания); SQLite-слой — без
   expiry (или retention недели/месяцы, напр. 30 дней).
2. **Sweeper не трогает сессию с активным шагом.** `SessionStore._sweep` и
   `SessionSQLiteStore.sweep_expired` должны учитывать `step_active` — иначе шаг
   длиннее TTL вытесняет сессию посреди работы (WS жив, сессии нет).
3. **Развести статусы завершения:** `completed` / `failed` (завис/ошибка) /
   `cancelled` (стоп-кнопка) / `interrupted` (рестарт/вытеснение — можно
   повторить). Таймаут никогда не должен давать статус, похожий на «готово»
   или зомби-`running`.
4. **Жёсткий `RUN_STEP_TIMEOUT_SEC=300` заменить stall-детекцией**: убивать шаг
   только когда **нет прогресса/событий N минут** (дефолт ~180–300с), а дедлайн
   сделать конфигурируемым и > реального худшего легального шага (PDF-парсинг +
   индексация + генерация 1–5 мин). Hard ceiling — только последний предохранитель.
5. **`WS_IDLE_TIMEOUT_SEC` и `RUN_STEP_TIMEOUT_SEC` не должны быть случайно
   равны** (сейчас оба 300). WS-idle — чисто «транспортная» ручка (heartbeat
   держит открытым независимо), step-timeout — «прогрессовая».
6. **Heartbeat (15с) слать в любой фазе хода**, а не только внутри `run_step`
   (между шагами, в тихих под-операциях), чтобы фронтенд-`ANSWER_TIMEOUT` (120с)
   не сработал при живом WS.
7. **Per-turn event log с `seq` + resume.** При обрыве WS клиент донагоняет
   события с `last_seq` вместо перезапуска хода; при рестарте шаг со статусом
   `running`, у которого нет живого таска → `interrupted`/`failed` + «повторите»,
   и никогда не оставлять `step_active=True` (это навсегда сломает WS-guard).
8. **Restore-семантика:** рестарт восстанавливает диалог и завершённые шаги;
   шаг, шёл на момент краша → `interrupted`, `step_active=False`, предложить
   переслать. Текущее «restore if not quiz_complete» не покрывает mid-step.
9. **Cancel ≠ timeout ≠ failed** — разные UI/API концепции: явный Stop →
   `cancelled` (уведомить по WS); stall/рестарт → `interrupted`.

## 3. Pitfalls текущей реализации (зафиксировать как чек-лист при внедрении)

- `api/engine.py:36` `WS_IDLE_TIMEOUT_SEC=300` == `RUN_STEP_TIMEOUT_SEC=300`
  (`src/config.py:183`): WS живёт ровно столько, сколько шаг, который защищает.
- `SessionStore._sweep` (`api/engine.py:341-353`) не смотрит на `step_active`:
  шаг > 30 мин вытеснит сессию.
- `restore_or_create` (`api/engine.py:464`) — **мёртвый код**: вызывается только из
  `scripts/test_restore_topic.py`, прод всегда делает `store.create`
  (`api/routes/sessions.py:42`, `api/app.py:58`). Restore-механизм надо
  проектировать с нуля (эндпоинт resume по `student_id`, правила отбора).
- `SessionSQLiteStore.sweep_expired` (`src/session_store.py:109`) — **нигде не
  вызывается**; к тому же `step_active` — поле `SessionData` (`api/engine.py:190`),
  не персистится в `TutorState`, поэтому SQLite-свипер не может знать об активном
  шаге без изменения схемы.
- `SESSION_IDLE_TTL_SEC` (`src/config.py:181`) — **мёртвый конфиг**: `SessionStore`
  хардкодит `ttl=1800.0` (`api/engine.py:211`), `.env` не влияет на TTL.
- **Ключевой пропущенный сценарий:** WS закрывается кодом **1000** при idle≥300с
  (`api/routes/messages.py:83-85`), а фронт на код 1000 **не переподключается**
  (`frontend/src/App.jsx:541`). Следующий шаг уходит в `session.queue` без
  потребителя → UI висит в busy до `ANSWER_TIMEOUT` (120с, `App.jsx:222`) →
  ответ «навсегда потерян» после ~5 мин бездействия. Это и есть исходная жалоба.
- `asyncio.wait_for` (`api/engine.py:584`) отменяет future, но worker-поток
  `to_thread(_invoke)` **продолжает работать**: шлёт WS-события и жжёт LLM-бюджет
  после пометки сессии `failed`. POST /cancel кооперативен (`engine.py:553-555`) и
  не прервёт зависший LLM-вызов/OCR. → нужна обработка orphan-потоков.
- Нет replay-лога событий → при обрыве WS фронтенд может висеть в `isStreaming`
  до heartbeat-timeout.
- Фронтенд `ANSWER_TIMEOUT=120s` (`frontend/src/App.jsx:222`) спасается только
  heartbeat'ом; если какая-то фаза > 2 мин вообще не шлёт событий — таймаут.

## 3a. Коррективы по результатам критики (2026-09-01)

Вердикт: 🟡 доработать. Диагностика частично верна, но ряд пунктов был против
несуществующего кода. Уточнения и порядок работ:

1. **«Сессия жива»**: единственная причина реальной «смерти в процессе работы»
   сегодня — **WS-close кодом 1000 по idle без reconnect**. Требование №1:
   keepalive в idle-фазе (heartbeat не только внутри `run_step`) **или**
   reconnect+replay по действию пользователя. Связать с п.5/п.7 рекомендаций.
2. **`restore_or_create`/`sweep_expired` — вычеркнуть из критики** как мёртвый
   код; сформулировать вместо них «спроектировать и подключить resume» (эндпоинт,
   правила отбора по student_id, последняя незавершённая сессия).
3. **Порядок работ:** (a) heartbeat-везде → (b) stall-детекция (порог по фазам с
   гарантированным событием, не единый 180с) → (c) только потом повышение
   дедлайна шага; `_sweep` по `step_active` — предусловие для (c), а не
   автономный пункт.
4. **Stall ≠ hard kill:** убрать «заменить RUN_STEP_TIMEOUT на stall» — оставить
   hard ceiling как последний предохранитель, добавить обработку orphan-потоков
   (поколение шага в событиях, игнор событий от отменённого шага).
5. **Event log и resume mid-step — два разных артефакта:** replay WS (`seq`,
   MVP) отдельно от resume незавершённого шага (нужен checkpointer —
   `src/graph.py:2328` сейчас `compile(checkpointer=None)`).
6. **Статусы — явным скоупом, а не «развести»:** расширить
   `session_status: Literal[...]` (`src/states.py:123`) → каскад:
   `message_response` (`engine.py:651,669`), маршрутизация графа
   (`graph.py:661,667,881,1844,1857`), `src/evaluation.py:292`,
   `src/agent_tools.py:301`, экспорт (`src/export.py:29`), тесты
   (`tests/test_graph.py:459,771,854`, `tests/test_api.py:443`). Скоординировать
   с параллельными правками коллеги в `src/evaluation.py`/`src/graph.py`.
7. **Подключить `SESSION_IDLE_TTL_SEC`** к `SessionStore.__init__` (`engine.py:211`)
   или удалить поле. SQLite-таблица растёт бесконечно — добавить retention.

**Инвариант тестов:** фактически в `tests/` 742 test-функции, не ~550 — базовый
зелёный срез считать по фактическому числу.

## 4. Референсы

- DeepTutor: `unified_ws.py`, `turn_runtime.py`, `sqlite_store.py`,
  `unified_session_manager.py`, web: `unified-ws.ts`, `reconnecting-websocket.ts`
- llamatutor: `app/page.tsx`, `app/api/getChat/route.ts`, `utils/TogetherAIStream.ts`
- LangGraph persistence docs; Vercel functions duration/streaming/runtimes docs

## 5. Что делать дальше (открытые пункты)

- [ ] **Требование №1:** решить сценарий «WS закрыт кодом 1000 → шаг потерян»
      (keepalive в idle-фазе или reconnect+replay по действию пользователя).
- [ ] Согласовать целевые значения: WS-idle (транспорт), step-stall (прогресс),
      in-memory TTL (кэш), SQLite retention — как независимые конфиги;
      подключить `SESSION_IDLE_TTL_SEC`.
- [ ] Решение: персистентный event log (`seq`) в SQLite поверх текущей модели —
      это предпосылка для надёжного resume WS (артефакт 1). Resume mid-step
      (артефакт 2) — отдельно, через checkpointer.
- [ ] Обработка orphan-потоков после `asyncio.wait_for` (поколение шага, игнор
      событий от отменённого шага).
- [ ] Расширение статусов — явным скоупом с каскадом правок и тестами.
- [ ] План реализации (см. roadmap #9): изменения в `api/engine.py`,
      `src/session_store.py`, `src/config.py`, `src/states.py`, `frontend/src/App.jsx`.
