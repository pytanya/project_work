# EduTutor — образовательный агент-тьютор (MVP)

Адаптивный тьюторинг для студентов и школьников: авто-поиск учебного материала
в легальных источниках → разбор → квизы → объяснение ошибок → карта знаний.

Спецификация-контракт: [`SPECIFICATION.md`](SPECIFICATION.md) (разделы 1–7, 12 — MVP).

## Сценарий (одна строка)

```
Python 3.11+ → Docling/pdfplumber (PDF) → Chunks → Qdrant/NumpyStore (embeddings
RouterAI или sentence-transformers) → LangGraph (intake → источник → квиз → оценка
→ судья) → CLI-вывод
```

```
┌────────┐  ┌──────────────────┐  ┌─────────────────────┐  ┌───────────────────┐
│ Intake │→ │ Поиск источника   │→ │  Квиз (RAG + LLM)   │→ │ Оценка + судья    │
│ чек-лист│ │ Plan B (PDF)/Plan A│  │ дешёвая/тьютор-модель│  │ TUTOR/EXPERT+Judge│
└────────┘  └──────────────────┘  └─────────────────────┘  └───────────────────┘
     ↑  уточнения (≤8 итераций, 2 без прогресса → экстренный старт)
```

## Установка (кросплатформенно)

### 0. Системные требования (один раз)

`torch` (sentence-transformers) и `chromadb` (rust-binding) требуют системного
рантайма/компилятора. Без него работают альтернативные бэкенды по умолчанию
(`EMBEDDING_PROVIDER=api`, `VECTOR_STORE=qdrant`/`numpy`) — устанавливать НЕ обязательно.
Qdrant — основной векторный бэкенд (`VECTOR_STORE=qdrant`): server (`QDRANT_URL`,
docker-compose.yml) или embedded (`QDRANT_PATH`, без Docker). ChromaDB — опционально.

| ОС | Что установить | Команда |
|----|----------------|---------|
| **Windows** | Microsoft Visual C++ Redistributable (x64) | скачать и запустить https://aka.ms/vs/17/release/vc_redist.x64.exe |
| **macOS** | Command Line Tools (Xcode) | `xcode-select --install` |
| **Linux** (Debian/Ubuntu) | build-essential + python3-dev | `sudo apt-get update && sudo apt-get install -y build-essential python3-dev` |
| **Linux** (Fedora/RHEL) | Development Tools + python3-devel | `sudo dnf groupinstall "Development Tools" && sudo dnf install python3-devel` |

### 1. Python-окружение

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.\.venv\Scripts\Activate.ps1     # Windows (PowerShell)
pip install -r requirements.txt

# Браузер для crawl4ai (dynamic rendering, расширение заказчика)
python -m playwright install chromium

# Конфигурация
cp .env.example .env             # Linux/macOS
copy .env.example .env           # Windows — заполнить ROUTERAI_API_KEY
```

## Запуск (MVP — консольное демо)

```bash
# Интерактивный прогон сценария «школьник 6 класс, география» (Plan B: локальный PDF)
python main.py --scenario schoolchild_grade6_geography

# Автоматический прогон (3 вопроса)
python main.py --scenario schoolchild_grade6_geography --auto --questions 3

# Офлайн-демо (без сети/LLM)
python main.py --scenario schoolchild_grade6_geography --mock
```

Сценарии описаны в [`evals/golden_set.json`](evals/golden_set.json):
`schoolchild_grade6_geography`, `student_with_pdf`, `student_no_textbook`, `no_materials`.

## API (FastAPI + WebSocket, раздел 8) и UI (React, раздел 9)

```bash
uvicorn api.app:app --host 0.0.0.0 --port 8000   # API: http://127.0.0.1:8000/docs
cd frontend && npm install && npm run dev        # UI: http://localhost:5173
```

REST: сессии, intake, upload, find-textbook, message, topic, graph, cancel, history, health, metrics.
WS `/api/sessions/{id}/ws`: `intake.question`, `source.progress`, `quiz.card`,
`tutor.explanation`, `tutor.lesson`, `tutor.summary`, `graph.ready`, `source.failed`.

## Qdrant векторное хранилище (roadmap #1)

Два режима — server и embedded:

```bash
# Режим 1: Qdrant-сервер в Docker (docker compose up -d qdrant)
docker compose up -d qdrant
#   .env: VECTOR_STORE=qdrant, QDRANT_URL=http://localhost:6333

# Режим 2: embedded (локальная персистентная БД, БЕЗ Docker)
#   .env: VECTOR_STORE=qdrant, QDRANT_PATH=./data/qdrant

# Режим 3: текущий портативный бэкенд (без Qdrant)
#   .env: VECTOR_STORE=numpy
```

Активный бэкенд виден в `GET /api/health` (`vector_store` + `collection`).

## Профили учеников и персональная база знаний

База знаний (Wiki/мастерство/заметки) **персональная**: ученики разных классов не
смешивают свои данные. Каждый ученик получает стабильный `student_id` (хранится в
localStorage фронта / передаётся в `POST /api/sessions`), а статьи лежат в
`data/knowledge_wiki/<student_id>/<subject>/<topic>.md`.

- **Профиль** (`data/students/<student_id>.json`): имя, тип, класс — заполняется
  один раз и персистентно; следующие сессии того же ученика начинаются с префиллом.
- **Быстрая карточка знакомства** (`agent_card`): вместо пошаговых вопросов агент
  показывает форму (имя, тип, класс, предмет, тема, учебник, режим). Заполнение —
  одним `POST /api/sessions/{id}/intake/card` (обычный текстовый `/intake` остаётся
  как fallback). Имя/тип/класс из карточки сохраняются в профиль.
- **Изоляция**: `/api/wiki?student_id=`, `/graph` mastery-слой и drill-down
  (`/graph/{node}/wiki`) читают статьи только этого ученика.

```bash
# Создание сессии для конкретного ученика (вернувшийся — из localStorage)
curl -X POST http://127.0.0.1:8000/api/sessions \
  -H 'Content-Type: application/json' \
  -d '{"student_id": "stu_1a2b3c"}'
# → {"session_id": "...", "student_id": "stu_1a2b3c", "student_name": ""}

# Персональная база знаний ученика
curl 'http://127.0.0.1:8000/api/wiki?student_id=stu_1a2b3c'
```

## База знаний (Knowledge Wiki, roadmap #2)

Между сессиями накапливаются wiki-статьи по темам
(`data/knowledge_wiki/<student_id>/<subject>/<topic>.md`, OKF v0.2): мастерство,
попытки, правильные ответы, заметки об ошибках.

```bash
# API
curl http://127.0.0.1:8000/api/wiki                       # сводка: предмет → темы
curl http://127.0.0.1:8000/api/wiki/философия             # статьи предмета
curl http://127.0.0.1:8000/api/wiki/философия/Кант        # статья темы
```

В UI — панель «База знаний» (donut-диаграмма тем + tooltip: мастерство, точность,
заметки). Обновляется при каждом ответе (`apply_record`) и завершении квиза
(`sync_mastery`). Тело статьи генерируется Wiki-LLM из RAG-контекста (`enrich_body`).

### Автоматическая обработка форматов заметок

Компоненты фронтенда (`NoteItem`, `TopicModal`) принимают notes массив, содержащий
смесь объектов и legacy-строк. Парсинг выполняется внутри `NoteItem` через
`normalizeNote()`, поэтому родительским компонентам не нужно разделять форматы.

### Граф знаний (roadmap #3)

- **Mastery overlay** — цвет узла = уровень усвоения (зелёный/жёлтый/красный) из wiki
- **Типы рёбер** — `part_of`/`prerequisite`/`related` разными цветами + легенда
- **Zoom/pan/drag** на SVG (колесо — масштаб, drag — сдвиг)
- **Drill-down** — клик по узлу → wiki-статья (mastery, заметки, тело)

> **Фаза 1+2 (август 2026):** 
> - Frontend оптимизации: settings-gear с toggle «Быстрый ответ», разделение busy/uploadBusy/chatBusy, typing indicator, счётчик «Вопрос N/M» в QuizCard
> - Бэкенд SQLite-персистентность сессий (`src/session_store.py`) — состояние сохраняется/восстанавливается между перезапусками сервера
> - **RAG + адаптация (Фаза 3):** hybrid retrieval (BM25 + RRF, `src/knowledge.py:HybridVectorStore`) и LinUCB contextual bandit для выбора сложности (`src/adaptive.py`)
> - **Knowledge Wiki (roadmap #2):** идемпотентное накопление знаний между сессиями; граф веб-источников — по страницам (book → page → subtopics), шум отсечён; источники+автор видны в панели «Источник»

### Граф знаний и подготовка по темам

После индексации учебника строится граф тем (`data/knowledge_graphs/<hash>.json`),
и агент **ждёт выбора темы** (гейт): `GET /graph` возвращает nodes/edges,
`POST /topic {topic_id}` активирует урок и генерирует вопрос по нему.
Граф кешируется по версии схемы + имени файла + размеру (инвалидация при смене структуры).

### Режимы

- `квиз` — вопросы по теме с адаптивной сложностью (↑ 3 верных, ↓ 2 ошибки), знаниевая карта, объяснение ошибки с цитатой §N.
- `урок` — перед квизом тьютор объясняет тему по RAG-контексту, спрашивает «готов к квизу?» (да/нет).
- `объяснение`, `глубокий разбор` — пояснения и экспертные разборы.

## Адаптивное обучение (roadmap #6–8, 2026-08-31)

Контур «ошибка → знание → повторение»: Student Knowledge Graph, scaffolding и
интервальное повторение SM-2. Архитектурные идеи — из DeepTutor (mastery-гейт,
Question Bank) и LlamaTutor (динамический curriculum).

**1. Student Knowledge Graph** (`src/student_kg.py`, JSON-профиль в
`data/students/<sid>.json`): темы со статусами `not_studied/in_progress/mastered`,
mastery, weak_areas, relations. Обновляется на каждом ответе (`evaluation.sync_student_kg`),
при уроке (`in_progress`) и завершении квиза; relations — из графа учебника.
При выборе темы с неосвоенным пререквизитом агент шлёт `system` `kind="mastery.gate"`.
API: `GET/POST /api/students/{id}/knowledge-graph`, `GET .../recommendations`.

**2. Scaffolding** (`src/scaffold.py`, флаг `ENABLE_SCAFFOLDING`): при ошибке агент
не финализирует вопрос сразу, а даёт подсказку (`quiz.hint`, уровень 1 «наводящая»,
уровень 2 «начни так», лимит `MAX_HINTS_PER_QUESTION`); после исчерпания — объяснение
или пошаговая декомпозиция (`QuizCard.subtasks`, узел `subtask_node`, возврат к
исходному вопросу). В агентном режиме — инструмент `give_hint`.

**3. Spaced Repetition** (`src/review.py`, флаг `ENABLE_SPACED_REPETITION`): ошибочные
вопросы → карточки SM-2 в `data/review_bank/<sid>.json` (дедуп по хэшу). Блиц-опрос
по должным карточкам запускается по запросу:

```bash
# API
curl http://127.0.0.1:8000/api/students/stu_1a2b3c/review          # stats + due-карточки
curl -X POST http://127.0.0.1:8000/api/sessions/{sid}/review        # запуск блица

# CLI
python main.py --scenario schoolchild_grade6_geography --mock --review
```

В UI — кнопка «Повторить (N)» в панели «Мои знания», бейдж «повторение» на карточке,
hint/review-пузыри в чате. Агентный режим: инструменты `start_review`/`submit_review`.

## Наблюдаемость, ограничения и SOP

### Наблюдаемость (JSONL-трассировка запроса)

Каждый запрос трассируется по этапам с уникальным `request_id`:
`logs/run_<timestamp>.jsonl` (CLI) и `logs/session_<sid>.jsonl` (API). Записи:
`node:*` (вход/выход узлов графа с длительностью), `agent.action` (вызов инструмента
модели: status/args/reason), `user_request` (начало/конец обработки сообщения).
Чувствительные данные (ключи, email) маскируются (`mask_sensitive`).

### Guardrails и лимиты

- **Входной фильтр** (`guard_user_input`): prompt-injection + контент-фильтр на
  `POST /api/sessions/{id}/message`, `POST /intake` и в CLI — заблокированный текст
  не передаётся агенту (возвращается `error`/предупреждение).
- **Circuit breaker**: серия сбоев шага графа (`CIRCUIT_BREAKER_THRESHOLD`, по умолч. 3)
  → защитная пауза `CIRCUIT_BREAKER_COOLDOWN_SEC` (fail closed); состояние видно в
  `GET /api/health` (`circuit_breaker`).
- **Бюджеты** (`BudgetGuard`, В-7): `MAX_COST_USD`, `CHEAP/TUTOR/JUDGE_ALLOWANCE_USD`,
  `MAX_LLM_CALLS_PER_SESSION` — `LLMClient` блокирует вызовы при превышении
  (`BudgetExceededError` → понятное сообщение пользователю).
- Плюс retry с backoff (429/5xx), таймауты шага/запроса, лимиты итераций intake и OCR,
  антидубликат вопросов.

### SOP: когда и как агент использует инструменты

- **Интервью (intake)** — `interview_progress`, `extract_intake_fields`, `set_intake`:
  модель ведёт чек-лист, после каждого ответа извлекает поля и подтверждает прогресс;
  при достаточности данных вызывает `route_to_source`. Поверх — детерминированный
  слой-страховка (поля из ответа применяются всегда, даже если модель не вызвала инструмент).
- **Retrieval** — `rag_search`: модель решает, нужен ли поиск, формулирует запрос и
  при пустом результате может повторить или завершить; фильтры — subject/grade/раздел.
- **Тьюторинг** — `generate_lesson/quiz`, `evaluate_answer`, `explain_error`,
  `deep_dive`, `finish_session`: RAG-first — без контекста контент не генерируется
  (`generate_lesson`/`generate_quiz` вернут `{ok:false, required_action}`).
- **Ошибки инструмента** возвращаются модели как `{ok:false}` — модель сама решает,
  что дальше (повторить, сменить инструмент или завершить).

## Тесты и eval

```bash
python -m pytest tests/ -v              # 754 теста (юнит + интеграционные RouterAI)
python evals/edututor_eval.py --runs 3  # EduTutorEval: intake/find_textbook/judge + intent accuracy
python evals/edututor_eval.py --mock    # офлайн-режим
```

## Ответы на «Семь вопросов» памятки

| # | Вопрос | Ответ в коде |
|---|--------|-------------|
| 1 | Какую полезную задачу решает агент? | Адаптивный тьюторинг: генерация персонализированного урока → квиз с RAG-контекстом → оценка ответа → объяснение ошибок при неправильном ответе → накопление знаний в Wiki |
| 2 | Где агент сам выбирает следующее действие? | `src/graph.py:2345` — `USE_AGENT_TUTOR=True`: узел `NODE_AGENT_TUTOR` вызывает `run_tutor_agent()` (ReAct loop), модель выбирает tools (`rag_search`, `evaluate_answer`, `finish_session`) и решает, продолжать или завершать |
| 3 | Когда и почему каждая модель? | `tutor_llm` (qwen3.7-flash) → вопросы/уроки; `expert_llm` (deepseek-v4-flash) → deep-dive объяснения; `judge_llm` (gemini-3.5-flash-lite) → оценка качества; `agent_llm` (qwen3.7-flash) → ReAct loop с function calling |
| 4 | Какой инструмент через function calling? | `rag_search` (семантический поиск по индексу учебника), `evaluate_answer` (оценка ответа ученика), `finish_session` (завершение сессии), `set_intake` (интейк поля) — см. `src/agent_tools.py:350-367` |
| 5 | Когда агент обращается к памяти? | `rag_search` вызывается в `content_node` когда контекста недостаточно для ответа, и в `agent_loop` когда модель решит что нужен внешний контекст. Если пусто → сообщает об отсутствии материала |
| 6 | Ветвление/повтор/условие остановки? | Conditional edges: `route_learner`→`route_grade`→`route_source`; retry: `QUESTION_DEDUPE_RETRIES=2` при дубликатах; stop: `quiz_complete=true`, `MAX_LLM_CALLS_PER_SESSION`, circuit breaker |
| 7 | Как доказать корректность? | 754 unit/integration тестов; eval suite (`evals/edututor_eval.py`); metrics collector (стоимость/токены/латентность); JSONL tracing с уникальным `req_{sid}`; budget guard + circuit breaker |

## Известные ограничения (Known Issues)

## Ключевые решения и компромиссы

| Вопрос | Решение |
|--------|---------|
| Embeddings | `EMBEDDING_PROVIDER=api` (RouterAI `intfloat/multilingual-e5-large`) — без локального torch/MSVC; `local` (sentence-transformers e5-small) — после установки VC++; ретраи с бэк-оффом на 429/5xx/таймаут |
| Векторное хранилище | `VECTOR_STORE=numpy` (портативный, без MSVC); `chroma` (ChromaDB); `qdrant` (roadmap #1) — server (`QDRANT_URL`, docker-compose.yml) или embedded (`QDRANT_PATH`, без Docker) |
| **Hybrid RAG** (7.2) | `HYBRID_RAG=true`: векторный поиск + **BM25 (Okapi)** с fusion через **RRF** (обёртка `HybridVectorStore` над любым VectorStore, чистый Python без зависимостей) |
| **Адаптивная сложность** | `ADAPTIVE_BANDIT=true`: **LinUCB contextual bandit** (руки = сложности, контекст = мастерство темы/класс/недавний результат, награда = score оценки); `false` — эвристика «3 верных → ↑, 2 ошибки → ↓» |
| Судья (К-4) | Gemini на RouterAI (без VPN); OpenRouter для судьи не используется |
| Источники (К-2) | Только легальные: локальные PDF (Plan B), Викиучебник, открытые страницы; капчу не обходим |
| Сканы (3.2) | `detect_text_layer` → агент просит **страницы + тему** → OCR **только их** (`ocr_pages`, ru+en) с буфером `OCR_PAGE_BUFFER` и автодетекцией смещения номера; валидация темы; CPU-OCR медленный — поэтому только нужные страницы. **OCR-движок — EasyOCR** (лёгкий, без MSVC). PaddleOCR (точнее для русского, быстрее на CPU) отклонён: `paddlepaddle` ~500МБ и конфликтует с принципом портативности MVP; при необходимости замена — через абстракцию OCR-провайдера с нормализацией формата к `(bbox, text, conf)` |
| Дешёвые роли (В-2) | `CHEAP_MODEL=google/gemma-3-4b-it` на RouterAI; при отказе — fallback TUTOR_MODEL |
| **Quick answer toggle** (Фаза 1) | Popup settings ⚙ → quickAnswer: true = автоотправка мгновенно; false = полоса подтверждения «Вы выбрали X» |
| **Разделение busy** (Фаза 1) | uploadBusy (индексация) / chatBusy (квиз); Banner индексации не мешает квизу |
| SQLite персистентность (Фаза 2) | `SessionSQLiteStore` сохраняет состояние после каждого шага в `data/session_persist.db`. Восстановление сессий между перезапусками сервера (`restore_or_create`) — TODO: вызывается только из тестов, не из production-потока `create_session`. |

## Известные ограничения (Known Issues)

1. **Авто-поиск учебника нестабилен в РФ** — lesson.edu.ru → 401, Stepik → DNS-block, РЭШ → 503 anti-bot, ФИПИ → 403. Работает только DuckDuckGo Search (ddgs), но он медленный и не гарантирует образовательный контент. Основной путь — загрузка локального PDF.
2. **`restore_or_create` — мёртвый код** — метод существует в `SessionStore`, но не вызывается при создании сессии (production использует `store.create()`). Сессии не восстанавливаются между перезапусками сервера.
3. **SESSION_IDLE_TTL_SEC** — конфиг в `config.py:181` используется через `SESSION_STORE` и актуален, но в roadmap §9 упоминается как «мёртвый» — это ошибка документации.
4. **Индексация PDF может занимать 1-2 минуты** — парсинг + чанкинг + эмбеддинги выполняются синхронно на первом шаге. Фронт показывает прогресс-баннер, но без детализации.

## Пример запроса и результата

Сценарий: ученик 6 класса, география, тема «Оборот воды в природе».

```bash
# Создание сессии + авто-интейк (через карточку знакомства)
curl -X POST http://127.0.0.1:8000/api/sessions \
  -H 'Content-Type: application/json' \
  -d '{"student_id": "stu_demo_ivan", "initial": {"learner_type":"schoolchild","grade":"6","subject":"география","topic":"оборот воды"}}'

# Ответ: {"session_id": "a3f9k2", "student_id": "stu_demo_ivan", "student_name": ""}

# Отправка ответа на вопрос квиза
curl -X POST http://127.0.0.1:8000/api/sessions/a3f9k2/message \
  -H 'Content-Type: application/json' \
  -d '{"text": "Вода испаряется, поднимается вверх и конденсируется"}'

# Результат будет возвращён через WebSocket (event: tutor.explanation или quiz.card для следующего вопроса)
```

Лог сессии (`logs/session_a3f9k2.jsonl`) содержит трассировку каждого шага:
```jsonl
{"ts": "2026-09-01T14:30:01", "request_id": "req_a3f9k2", "step": "source_find", "action": "find_textbook", "status": "completed", "model": "qwen/qwen3.7-flash", "duration_ms": 12500}
{"ts": "2026-09-01T14:30:15", "request_id": "req_a3f9k2", "step": "quiz_card", "action": "generate_question", "status": "completed", "model": "qwen/qwen3.7-flash", "tokens": 342, "cost_usd": 0.001}
{"ts": "2026-09-01T14:30:28", "request_id": "req_a3f9k2", "step": "evaluate_answer", "action": "check_answer", "status": "correct", "model": "google/gemma-3-4b-it", "judge_criteria": {"relevance": 0.9, "completeness": 0.7}}
```

Полный пример работает через:
- **Frontend UI**: откройте `http://localhost:5173` → заполните карточку → чат → квиз
- **CLI demo**: `python main.py --scenario schoolchild_grade6_geography --auto`
- **Swagger API**: `http://localhost:8000/docs` (автогенерированный интерфейс FastAPI)

## Структура

```
src/           config, llm_client, guardrails, nlp, intake, curriculum (ФГОС),
               knowledge (RAG), qdrant_store (Qdrant backend), adaptive (LinUCB),
               source_finder (crawl4ai), tutor, judge, graph, metrics
api/           Pydantic-схемы (IntakeStatusResponse, QuizCard, MessageResponse, WsEvent)
tests/         754+ тест
evals/         golden_set.json, intent_dataset.json, edututor_eval.py
main.py        CLI-демо
docker-compose.yml  Qdrant + backend + frontend (roadmap #1)
```
