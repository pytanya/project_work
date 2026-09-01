# Handoff: следующая сессия — редизайн EduTutor

> Назначение: передать контекст агенту в новой сессии. Вставить как есть
> в первое сообщение (или скопировать раздел «Промпт для агента»).

## Промпт для агента (вставить в новую сессию)

> Редизайн EduTutor. Контекст: исследование и критика уже сделаны и лежат в
> `plans/2026-09-01-session-lifetime-research.md` (раздел 3a), `plans/2026-09-01-uiux-spec.md`
> (разделы 3, 4, 4a), roadmap #9/#10. Работаем ОТ этих документов, а не с чистого листа.
>
> Обязательно:
> 1. Сначала прочитай `plans/2026-09-01-uiux-spec.md` полностью и сверь каждое требование
>    R1–R37 с фактическим кодом (`frontend/src/App.jsx`, `frontend/src/components/*`) —
>    критик уже нашёл расхождения (donut→bar-полосы, SVG→Canvas, R34 частично есть).
>    Отметь для каждого R: есть/частично/нет.
> 2. Начни с MVP-набора из раздела 3 спеки. Для каждого MVP-пункта добавь тест в
>    `frontend/src/__tests__/` как критерий приёмки.
> 3. Порядок по roadmap #9 (session lifetime): reconnect на код 1000 уже сделан — не
>    повторяй. Дальше: resume-механизм (эндпоинт по student_id, сейчас `restore_or_create`
>    — мёртвый код), подключение `SessionSQLiteStore.sweep_expired`, stall-детекция,
>    расширение статусов (`src/states.py:123` — каскад правок).
> 4. ВАЖНО: не ломай инварианты — гибрид FSM+агент, fail-soft, никаких новых
>    зависимостей, тесты зелёные. Базовый срез — 742 бэкенд + 77 фронтенд-тестов.
> 5. Известные предсуществующие падения (НЕ трогать без задачи): `test_find_textbook_mock`
>    (нужна сеть), `test_graph_has_mastery_overlay` (зависит от RAG/graph).
> 6. Не переписывай рабочие функции «на всякий случай» — редизайн по чек-листу спеки,
>    по одному пункту, с тестом на каждый.

## Состояние на момент передачи

### Что уже сделано (не переделывать)

| Пункт | Файл | Статус |
|---|---|---|
| Reconnect WS на код 1000 (код 4004 — без реконнекта) | `frontend/src/App.jsx:543-552` | ✅ тесты 77/77 |
| `SESSION_IDLE_TTL_SEC` читается из settings | `api/engine.py:225-226` | ✅ `test_ttl_from_settings` |
| `_sweep` не вытесняет сессии с активным шагом | `api/engine.py:345-348` | ✅ `test_sweep_keeps_active_step` |

⚠️ На момент передачи эти правки в `api/engine.py`, `frontend/src/App.jsx`,
`roadmap.md`, `tests/test_api.py` **могут быть не закоммичены** — проверь `git status`.

### Документы-источники

- `plans/2026-09-01-session-lifetime-research.md` — §1 сравнительная таблица
  (DeepTutor/llamatutor/LangGraph), §2 рекомендации, §3 pitfalls, §3a коррективы критика.
- `plans/2026-09-01-uiux-spec.md` — §2 чек-лист R1–R37, §3 приоритеты MVP/nice-to-have,
  §4 сверка с реализацией, §4a зависимости от бэкенда.
- `roadmap.md` #9 (session lifetime) и #10 (UI/UX).

### Инварианты проекта

- Гибрид: детерминированная FSM (дефолт) + агентный цикл `USE_AGENT_TUTOR` (опция).
- fail-soft: любой сбой подсистемы не ломает текущий квиз.
- Никаких новых зависимостей.
- Атомарная запись файлов: tmp + rename (паттерн `StudentStore.save`).
- Тесты зелёные. Базовый срез: **742** бэкенд + **77** фронтенд-тестов
  (не ~550, как написано в старых README).

### Предсуществующие падения (не трогать без задачи)

- `tests/test_api.py::TestFindTextbook::test_find_textbook_mock` — требует сеть
  (Stepik `getaddrinfo failed`, нет модуля `duckduckgo_search`).
- `tests/test_api.py::TestGraph::test_graph_has_mastery_overlay` — зависит от
  RAG-чанков/graph (падёт и на чистом базисе — проверено через `git stash`).

## Очередь работ (по roadmap #9/#10)

### Roadmap #9 — session lifetime (после уже сделанного)

1. Resume-механизм: эндпоинт по `student_id` (сейчас `restore_or_create` в
   `api/engine.py:464` — мёртвый код, прод всегда `store.create`); правила отбора
   последней незавершённой сессии.
2. Подключить `SessionSQLiteStore.sweep_expired` (`src/session_store.py:109`,
   сейчас не вызывается) + SQLite retention (таблица растёт бесконечно).
3. Stall-детекция: порог по фазам с гарантированным событием; `RUN_STEP_TIMEOUT_SEC`
   — последний предохранитель. **Предусловие:** heartbeat во всех фазах хода.
4. Heartbeat (15с) в любой фазе хода, а не только внутри `run_step`.
5. `WS_IDLE_TIMEOUT_SEC` (транспорт) и step-timeout (прогресс) — независимые конфиги.
6. Статусы `completed`/`failed`/`cancelled`/`interrupted` — расширение
   `session_status` (`src/states.py:123`) с каскадом: `message_response`
   (`engine.py:651,669`), маршруты графа (`graph.py:661,667,881,1844,1857`),
   `src/evaluation.py:292`, `src/agent_tools.py:301`, экспорт (`src/export.py:29`),
   тесты. При рестарте mid-step шаг → `interrupted` + «повторите», `step_active=False`.
7. Per-turn event log с `seq` + resume WS с `last_seq` (артефакт 1) и resume
   mid-step через checkpointer (артефакт 2; `graph.py:2328` сейчас
   `compile(checkpointer=None)`).

### Roadmap #10 — UI/UX (MVP-набор из спеки §3)

- R2 индикатор WS-соединения (онлайн/переподключение/офлайн + тултип)
- R8–R10 баннер прогресса + счётчик «идёт уже ~45 c» + «дыхание» + отмена между
  шагами; контракт таймаута `step.timeout` → карточка с «Повторить» + POST
  с сохранённым текстом
- R11–R13 оптимистичная отправка (есть), Stop-кнопка стриминга, меню
  «Повторить/Изменить/Удалить» у ответа
- R15–R19 квиз-карты: навигация чипами, мгновенная проверка (есть), гидратация
  фида при reload из `GET /session`/истории (без изменения бэкенд-момента отправки)
- R21 лестница подсказок (есть hint/review-пузыри)
- R27–R32 ошибки с действием (материалы не найдены / бюджет / сбой генерации /
  пустые состояния)
- R33–R37 доступность: live-region, focus-trap, `aria-pressed`, дублирование
  цвета иконками, `prefers-reduced-motion`
- Каждый MVP-пункт — с тестом-критерием приёмки

### Nice-to-have (после MVP)

- R14 suggested-next-question, R20 Follow-up-чат, R22 Socratic-приглашение,
  R23–R26 связь панелей и конфетти-мастерство, пофайловая отмена + частичное
  сохранение, полная модель фоновых задач с pause/resume.

## Как проверить

```bash
# Бэкенд (из корня)
.venv/Scripts/python -m pytest tests/test_api.py -q        # учесть 2 предсуществующих падения
.venv/Scripts/python -m pytest tests/ -q                    # полный срез (742)
# Фронтенд
cd frontend && npm test                                     # vitest, 77
```
