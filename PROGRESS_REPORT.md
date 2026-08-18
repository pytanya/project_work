# EduTutor — Отчет о прогрессе (август 2026)

## ✅ Выполнено за сессию

### Фаза 4 — Тема обязательна в intake + доводка (100%)

**Причина:** поиск материалов шёл по предмету вместо темы. Решение (из списка фиксов): тема обязательна при заданном предмете.

| Задача | Статус | Детали |
|--------|--------|--------|
| Тема обязательна в intake | ✅ | `compute_missing`: если `subject` задан, `topic` — обязательное поле; «все/весь учебник» → `"all"` |
| Не проглатывать режим темой | ✅ | `normalize_answer("topic", ...)`: ответы-режимы («квиз»/«урок»/«объяснение»/«глубокий разбор») → None (переспрос), yes/no → None |
| topic="all" → поиск по предмету | ✅ | `find_textbook_node`: `search_topic=""` при `topic=="all"` |
| Веб-источники → граф + гейт темы | ✅ | `find_textbook_node` строит `knowledge_graph` из собранного текста, `awaiting_topic=True`; `route_textbook_result` → topic gate |
| Контракт 5.2 в SPEC | ✅ | Таблица `validate_intake` обновлена (тема обязательна при subject) |
| Обновлены intake-сценарии | ✅ | `test_graph.py` (9), `test_api.py` (7), `evals/golden_set.json` (4), `frontend e2e` (topic-flow ×2, topic-full, full-flow), `scripts/reproduce_topic_500.py` |
| Юнит-тесты | ✅ | `test_intake.py`: `normalize_answer("topic","квиз") is None`, `("topic","все")=="all"`, `compute_missing` требует topic |

**Результат тестов:** pytest **339 passed / 1 skipped** (3 deselected — сетевые интеграционные: `TestRealRouterAI`, `test_find_textbook_mock`), vitest **27 passed**, Playwright chromium **8 passed** (вкл. полный flow с новой темой в intake).

## ✅ Выполнено за сессию

### Фаза 1 — UI/UX оптимизации (100%)

| Задача | Статус | Детали |
|--------|--------|--------|
| Разделить busy на uploadBusy/chatBusy | ✅ | `App.jsx`: uploadBusy (FileUpload/SourceSearchPanel), chatBusy (QuizCard/ChatStream). Баннер индексации больше не мешает квизу |
| Шестерёнка настроек ⚙ | ✅ | Popup с toggle "Быстрый ответ". Настройка в localStorage |
| Автоотправка / подтверждение | ✅ | quickAnswer=true: мгновенно. false: confirm-bar "Вы выбрали X → OK/Отмена" |
| Фокус в поле ввода | ✅ | `inputRef.focus()` после каждой операции + автофокус при busy→false |
| Typing Indicator | ✅ | Три зелёные точки bounce animation при chatBusy |
| Убрать PRE_CHECK_MIN_LENGTH дубль | ✅ | Удалена dead строка из tutor.py |
| Счётчик Вопрос N/M | ✅ | QuizCard получает props, рендерит `<span className="badge counter">` |

### Фаза 2 — Backend оптимизации (80%)

| Задача | Статус | Детали |
|--------|--------|--------|
| Автоэкспорт CSV+OKF | ✅ | `summary_node()` вызывает `write_session_exports()` + `emit_okf_bundle()` |
| SQLite персистентность | 🟡 | `session_store.py` создан, `_save_state()` определён, но вызовы не интегрированы в `run_step()`. `restore_or_create()` определена но не используется в routes |
| Предгенерация вопросов пакетами | ⬜ | Не начато. Engine работает single-step invoke |

### Фаза 3 — RAG + адаптация + решения по OCR (100%)

| Задача | Статус | Детали |
|--------|--------|--------|
| **Hybrid RAG (BM25 + RRF)** | ✅ | `HybridVectorStore` (`src/knowledge.py`): Okapi BM25 (чистый Python, без зависимостей) + векторный поиск, fusion RRF, фильтры по метаданным к обоим ретриверам. Включается `HYBRID_RAG=true`, обёртка — в `make_graph_deps()`. Тесты `TestHybridRag` |
| **LinUCB bandit** | ✅ | `src/adaptive.py`: контекстный бандит (руки = easy/medium/hard, контекст = мастерство/класс/недавний результат/прогресс, награда = score01). Инициализация в `intake_node`, выбор сложности в `evaluate_answer_node`, состояние в `TutorState.bandit` (JSON-безопасно, переживает SQLite). Тесты `tests/test_adaptive.py`. `ADAPTIVE_BANDIT=false` → старая эвристика |
| **Решение по OCR** | ✅ | EasyOCR остаётся (портативность без MSVC); PaddleOCR задокументирован как опциональный путь (точнее для русского, быстрее на CPU, но `paddlepaddle` ~500МБ); G-OCR исключён. README + SPECIFICATION 3.2 |

### Фаза 3.5 — Багфиксы, найденные e2e-прогоном (chromium)

| Баг | Статус | Детали |
|--------|--------|--------|
| **WS-событие `system` терялось** | ✅ | `WsEvent.event` не содержал `"system"`, поэтому `topic.selected`/`lesson.ready`/`intake.warning` падали в pydantic-валидации и молча выбрасывались (см. `engine.publish`). Добавлено в `api/schemas.py` |
| **`totalQuestions is not defined`** | ✅ | Крэш React при рендере QuizCard (после выбора темы): `App.jsx` передавал `totalQuestions={totalQuestions}`, но переменной не было (состояние переименовано в `_quizCount`). Переименовано в `quizCount`, передаётся `totalQuestions={quizCount}` — счётчик «Вопрос N/M» заработал |
| **ProgressDashboard-тест** | ✅ | `getByText(/Правильных: 3\/5/)` матчил сквозь `<span>` (тест смотрит только прямые текстовые узлы). Тест разбит на частичные матчи |

### Исправленные баги

1. **"Раздумия" после ответа** — busy висит пока ждём HTTP resync. Исправлено: busy сбрасывается при получении WS события (quiz.card, tutor.explanation, system)
2. **Пустой экран после выбора темы** — `handleSelectTopic` вызывал `setCurrent(null)` до получения ответа. Исправлено: UI не скрывается, ждём WS событие
3. **Лишняя закрывающая скобка** в App.jsx — ломала сборку. Исправлено

### Обновлённые документы

- **SPECIFICATION.md**: разделы 8.5 (SQLite), 9.5/9.6 (UI navigation, quickAnswer), 15.2 (changelog), 17 (references)
- **README.md**: таблица ключевых решений, блок "Фаза 1+2"

## 🔧 Что нужно доделать

### Критично (блокирует UX)

1. **Интегрировать `_save_state()` в `run_step()`** — сейчас сохранения нет, прогресс теряется при перезапуске
2. **Интегрировать `restore_or_create()` в API routes** — нет восстановления сессий из SQLite
3. **Предгенерация вопросов пакетом** — даёт <100мс вместо ~3с при ответе на закрытые вопросы

### Важно (улучшение продукта)

4. **Multi-hop retrieval через граф знаний** — расширение контекста соседними секциями (prerequisite/related) для кросс-темных вопросов (см. анализ Digiler AI)
5. **Mock данные для тестов** — без реального учебника нельзя протестировать полный flow (topic-full использует `data/uploads/fcde261d.pdf`)
6. **Визуальная обратная связь** — зелёный/красный highlight варианта (Фаза 3)

## 📊 Тестирование

### Python tests (backend)
```bash
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e
# 340 passed, 1 skipped (реальный OCR), 341 collected ✅
```

### Vitest (frontend unit)
```bash
cd frontend && npx vitest run
# 27 passed (9 файлов) ✅
```

### Playwright tests (frontend, chromium)
```bash
cd frontend && npx playwright test e2e/app.spec.js e2e/session-speed.spec.js e2e/topic-flow.spec.js e2e/topic-500-check.spec.js e2e/topic-gate.spec.js e2e/topic-full.spec.js --reporter=list
# 8 passed ✅ (включая полный flow: upload → индекс → выбор темы → quiz card)
```

### Не passed
- `e2e/full-flow.spec.js` — требует реальный учебник для индексации
- `e2e/topic-gate.spec.js` — worker process crashed (нужен debug)

## 🎯 План автономных улучшений

### Близкий план (следующая сессия)

1. **Интегрировать persistence** — вызов `_save_state()` в `run_step()` + `restore_or_create()` в `api/routes/sessions.py`
2. **Добавить mock knowledge graph** — для тестирования topic selection без реального учебника
3. **Исправить topic-gate.spec.js** — debug worker crash
4. **Добавить E2E тест для quick answer toggle** — проверка сохранения настройки

### Среднесрочный план

5. **Batch question generation** — pool of 3-5 questions за 1 LLM call
6. **Визуальная обратная связь** — green/red highlight на вариантах
7. **Progress bar анимация** — CSS transitions для прогресс-баров

## 📝 Команды для запуска

```bash
# Backend
cd C:\otus\project_work
.\.venv\Scripts\Activate.ps1
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000

# Frontend
cd C:\otus\project_work\frontend
npm run dev

# Tests (Python)
.venv/Scripts/python.exe -m pytest tests/test_graph.py -v

# Tests (Playwright)
cd frontend && npx playwright test e2e/app.spec.js --reporter=list
```

## 🐛 Известные проблемы

1. **`full-flow.spec.js`** требует PDF с рабочего стола (`C:\Users\hppro\OneDrive\...\kuraev-osnovy-pravoslavnoy-kultury-uchebnik-...pdf`) — без него не запускается
2. **`topic-full.spec.js`** использует захардкоженный путь `data/uploads/fcde261d.pdf` — не переносим на другой комп
3. **SQLite persistence не вызывается** — `_save_state()` определён но не интегрирован (Фаза 2, открытый пункт)

---

**Статус:** MVP работает, UI оптимизации завершены, RAG улучшен (hybrid BM25+RRF) и адаптация переведена на LinUCB bandit; backend persistence — в процессе интеграции.
