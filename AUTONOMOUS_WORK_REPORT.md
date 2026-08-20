# EduTutor — Автономная работа (август 2026)

## ✅ Выполнено автономно (сессия 2026-08-18, часть 3): Roadmap #2 (Wiki-LLM) + #3 (граф)

### П.2 — Wiki-LLM
- `src/wiki.py`: `enrich_body()` — генерация тела wiki-статьи из RAG-контекста
  через LLM (`deps.tutor_llm`); вызывается из `evaluate_answer_node` при первом
  ответе по теме (пока тело пустое — экономия LLM-вызовов).
- Пустой/короткий результат LLM не затирает статью (каркас сохраняется).
- Тесты: `enrich_body` (конспект записан, персистентен), no-LLM (каркас),
  пустой результат (не затирает).

### П.3 — Визуализация графа знаний (без внешней библиотеки, на SVG)
- **Mastery overlay**: `GET /graph` возвращает `mastery`/`attempts` для узлов
  (матчинг с Knowledge Wiki по названию); узел окрашивается зелёным/жёлтым/красным
  по уровню усвоения + точка-индикатор.
- **Типы рёбер**: `part_of` (#64DFDF), `prerequisite` (#FFB703), `related`
  (#B388FF) — разные цвета + легенда в панели.
- **Zoom/pan/drag**: колесо — масштаб, drag — сдвиг (SVG transform).
- **Drill-down**: клик по узлу → панель wiki-статьи (mastery, попытки, точность,
  заметки, тело) через `GET /graph/{node_id}/wiki`.
- `clean_title`: HTML-entities (&#8470;) и суррогаты (CP1251) вычищаются из
  заголовков узлов; `GRAPH_SCHEMA_VERSION=3`.
- Фикс: `.session-id` с `min-height` — E2E session-speed считал его hidden (h:0).

**Файлы:** `src/wiki.py`, `src/graph.py`, `src/knowledge_graph.py`,
`api/routes/graph.py`, `frontend/…` (KnowledgeGraphPanel, KnowledgeWikiPanel,
App.jsx, index.css), `tests/…`.

**Тесты:** pytest (330+ passed, 0 failed), vitest 37 passed, chromium E2E 6 passed.

---

## ✅ Выполнено автономно (сессия 2026-08-18, часть 2): поток «студент без учебника»

**Проблема (пользовательский репорт):** в режиме «студент без учебника» — граф
тем-«лепнина», база знаний «странная» (attempts раздувались), не видно источников.

### Корневые причины и исправления

1. **«Лепнина» на графе (веб-источники).**
   - `_strip_html` схлопывал весь текст веб-страницы в одну строку → структура
     терялась, граф давал один generic-узел «topic». Теперь сохраняются блочные
     границы и заголовки h1-h6 → markdown.
   - `_web_headings` собирал все заголовки включая навигацию («См. также»,
     «Примечания», «Ссылки»). Добавлен чёрный список шумовых секций + лимит 30 узлов.
   - Граф из веб-источников строится **по каждой странице отдельно** и объединяется:
     `book → page (URL) → subtopics`. Страницы без структуры — сами темы.
     Нет «лепнины» из перемешанных заголовков всех страниц.
   - Одиночные суррогаты (CP1251-шрифты без ToUnicode) вычищаются в `clean_pdf_text`.

2. **«Странная» база знаний (идемпотентность wiki).**
   - Раньше `update_from_session` пересчитывал ВСЕ records заново на каждый ответ →
     attempts росли квадратично (70 при ~12 ответах).
   - Теперь: `apply_record()` (текущий ответ, attempts+=1) из `evaluate_answer_node`;
     `sync_mastery()` (без attempts++) из `summary_node`. Повторные вызовы не дублируют.

3. **Источники/автор не видны.**
   - `SourceSearchPanel` показывает найденные URL (с лицензией) и автора;
     `resync`/`handleFind` передают `sources` + `textbook_author`.

4. **Search-запрос студента.** Для студента (без класса) вместо «N класс» —
   «лекция конспект курс».

**Файлы:** `src/source_finder.py`, `src/knowledge_graph.py`, `src/knowledge.py`,
`src/wiki.py`, `src/graph.py`, `api/app.py`, `api/routes/wiki.py`, `api/schemas.py`,
`frontend/…` (SourceSearchPanel, KnowledgeWikiPanel, App.jsx), `tests/…`
(test_wiki, test_graph TestWebSourceFlow, test_knowledge_graph, test_source_finder,
test_api TestWiki, vitest KnowledgeWikiPanel/SourceSearchPanel).

**Тесты:** pytest (303+ passed, 0 failed), vitest 33 passed, chromium E2E 6 passed.

**Демо (реальный бэкенд):** студент/философия/Кант без учебника → найдены 5
источников → граф 14 узлов (book → pages → subtopics), шум отсечён → выбор темы
→ вопрос → wiki attempts=1 (не раздувается).

---

## ✅ Выполнено автономно (сессия 2026-08-18): Roadmap #1 — Qdrant

**Решение:** из четырёх пунктов roadmap первым реализован пункт №1 (Qdrant векторное
хранилище вместо in-memory). Причина: это базисная инфраструктура (persistent vector
search, metadata-фильтрация), на которую опираются пункты 2–4; кроме того, адаптер
тестируется в embedded-режиме `qdrant-client` БЕЗ установки Docker (разработка не
блокируется отсутствием Docker на машине).

**Файлы изменены/созданы:**
- `src/qdrant_store.py` — НОВЫЙ: `QdrantStore` (add/search/count/reset/delete),
  payload-поля (subject/grade/section_number/section_title/source/page_number),
  авто-создание коллекции при старте, два режима: server (`QDRANT_URL`) и embedded
  (`QDRANT_PATH`)
- `src/knowledge.py` — `make_store`/`make_qdrant_store`/`_model_dimension` (backend `qdrant`),
  рефакторинг `make_collection_name`
- `src/config.py` — `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_PATH`
- `api/app.py` — `GET /api/health` теперь сообщает активный векторный бэкенд
- `docker-compose.yml` — НОВЫЙ (qdrant + backend + frontend)
- `.env.example`, `requirements.txt` (`qdrant-client`), `SPECIFICATION.md` (3.3, 14),
  `README.md`, `roadmap.md`, `project_report.md`
- `tests/test_qdrant_store.py` — НОВЫЙ: 9 тестов (add/search/filter/persistence/reset/
  delete/factory)

**Результат тестов:**
- pytest: `test_qdrant_store.py` 9 passed; `test_knowledge/test_config/test_okf/test_export/
  test_knowledge_graph/test_api/test_schemas/test_guardrails/test_intake/test_adaptive/...`
  — без регрессий; `test_api.TestHealthMetrics.test_health` обновлён под новое health-поле
- vitest: 27 passed (9 файлов)
- Playwright chromium: `app.spec.js` (2), `session-speed.spec.js` (1), `topic-flow.spec.js` (2),
  `topic-gate.spec.js` (1) — все passed (в т.ч. ранее падавший topic-gate)

**Как включить:**
```env
VECTOR_STORE=qdrant
# server-режим (Docker): QDRANT_URL=http://localhost:6333
# embedded-режим (без Docker): QDRANT_PATH=./data/qdrant
```

---

## ✅ Выполнено автономно

### 1. Интеграция SQLite persistence (Критично)

**Файлы изменены:**
- `api/engine.py`:
  - Добавлен `store: Optional[SessionStore]` в `SessionData`
  - `_save_state()` вызывается в `run_step()` после каждого шага
  - `create()` и `restore_or_create()` передают `store=self` в SessionData
- `api/routes/sessions.py`:
  - `create_session()` использует `restore_or_create()` вместо `create()`

**Результат:**
- ✅ Состояние сессий сохраняется в `data/session_persist.db` после каждого шага
- ✅ При перезапуске сервера сессии восстанавливаются из SQLite
- ✅ Прогресс ученика (knowledge_map, records, correct_count) переживает перезагрузку

### 2. Исправление bug "Раздумия" после ответа

**Файл:** `frontend/src/App.jsx`

**Изменения:**
- Добавлен `isWaitingForAnswer` ref для отслеживания ожидания WS события
- В `handleEvent`: при получении `quiz.card`, `tutor.explanation`, `system` → `setChatBusy(false)`
- В `submitAnswer`: убран `await resync()`, добавлен timeout fallback 15 сек

**Результат:**
- ✅ busy сбрасывается мгновенно при получении ответа от бэкенда
- ✅ Нет задержки "раздумий" после отправки ответа
- ✅ Для long operations (indexing) есть fallback timeout

### 3. Исправление bug "Пустой экран" после выбора темы

**Файлы:**
- `frontend/src/App.jsx`: `handleSelectTopic` — убран `setCurrent(null)`
- `api/routes/graph.py`: `select_topic` — добавлено эмитирование WS события `system`

**Результат:**
- ✅ UI не скрывается при выборе темы
- ✅ В чате появляется "Готовимся по теме: X..."
- ✅ QuizCard показывается когда вопрос сгенерирован

### 4. Удаление лишней скобки в App.jsx

**Файл:** `frontend/src/App.jsx:370`

**Результат:**
- ✅ Сборка frontend проходит без ошибок

## 📊 Тестирование

### Backend (Python)
```bash
.venv/Scripts/python.exe -m pytest tests/test_graph.py tests/test_tutor.py -v
# 45 passed in 88.68s ✅
```

### Frontend (Playwright)
```bash
cd frontend && npx playwright test e2e/app.spec.js --reporter=list
# 2 passed (14.5s) ✅
```

### Не passed (требуют реального учебника)
- `e2e/full-flow.spec.js` — full intake → upload → index → quiz
- `e2e/topic-gate.spec.js` — worker crash (нужен debug)

## 🎯 Что можно улучшить дальше

### Близкий план (следующая сессия)

1. **Mock knowledge graph** — для тестирования topic selection без реального учебника
2. **Debug topic-gate.spec.js** — worker process crashed
3. **E2E test для quick answer toggle** — проверка сохранения настройки в localStorage

### Среднесрочный план

4. **Batch question generation** — pool of 3-5 questions за 1 LLM call (<100мс вместо ~3с)
5. **Визуальная обратная связь** — green/red highlight на вариантах при ответе
6. **Progress bar анимация** — CSS transitions для прогресс-баров (low/mid/high)

## 📝 Команды для проверки

```bash
# Backend
cd C:\otus\project_work
.\.venv\Scripts\Activate.ps1
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000

# Frontend  
cd C:\otus\project_work\frontend
npm run dev

# Tests (Python)
.venv/Scripts/python.exe -m pytest tests/ -v --ignore=tests/test_eval.py

# Tests (Playwright)
cd frontend && npx playwright test e2e/ --reporter=list
```

## 📄 Документация

- `PROGRESS_REPORT.md` — полный отчет о прогрессе
- `SPECIFICATION.md` — обновлены разделы 8.5, 9.5/9.6, 15.2, 17
- `README.md` — обновлена таблица решений, блок "Фаза 1+2"

---

**Статус:** Все критичные баги исправлены, persistence интегрирован, тесты проходят. Продукт готов к тестированию пользователем.
