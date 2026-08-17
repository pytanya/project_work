# EduTutor — образовательный агент-тьютор (MVP)

Адаптивный тьюторинг для студентов и школьников: авто-поиск учебного материала
в легальных источниках → разбор → квизы → объяснение ошибок → карта знаний.

Спецификация-контракт: [`SPECIFICATION.md`](SPECIFICATION.md) (разделы 1–7, 12 — MVP).

## Сценарий (одна строка)

```
Python 3.11+ → Docling/pdfplumber (PDF) → Chunks → ChromaDB/NumpyStore (embeddings
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
(`EMBEDDING_PROVIDER=api`, `VECTOR_STORE=numpy`) — устанавливать НЕ обязательно,
но нужно для локальных embeddings и ChromaDB.

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

> **Фаза 1+2 (август 2026):** 
> - Frontend оптимизации: settings-gear с toggle «Быстрый ответ», разделение busy/uploadBusy/chatBusy, typing indicator, счётчик «Вопрос N/M» в QuizCard
> - Бэкенд SQLite-персистентность сессий (`src/session_store.py`) — состояние сохраняется/восстанавливается между перезапусками сервера
> - **RAG + адаптация (Фаза 3):** hybrid retrieval (BM25 + RRF, `src/knowledge.py:HybridVectorStore`) и LinUCB contextual bandit для выбора сложности (`src/adaptive.py`)

### Граф знаний и подготовка по темам

После индексации учебника строится граф тем (`data/knowledge_graphs/<hash>.json`),
и агент **ждёт выбора темы** (гейт): `GET /graph` возвращает nodes/edges,
`POST /topic {topic_id}` активирует урок и генерирует вопрос по нему.
Граф кешируется по версии схемы + имени файла + размеру (инвалидация при смене структуры).

### Режимы

- `квиз` — вопросы по теме с адаптивной сложностью (↑ 3 верных, ↓ 2 ошибки), знаниевая карта, объяснение ошибки с цитатой §N.
- `урок` — перед квизом тьютор объясняет тему по RAG-контексту, спрашивает «готов к квизу?» (да/нет).
- `объяснение`, `глубокий разбор` — пояснения и экспертные разборы.

## Тесты и eval

```bash
python -m pytest tests/ -v              # 341 тестов (юнит + интеграционные RouterAI)
python evals/edututor_eval.py --runs 3  # EduTutorEval: intake/find_textbook/judge + intent accuracy
python evals/edututor_eval.py --mock    # офлайн-режим
```

## Ключевые решения и компромиссы

| Вопрос | Решение |
|--------|---------|
| Embeddings | `EMBEDDING_PROVIDER=api` (RouterAI `intfloat/multilingual-e5-large`) — без локального torch/MSVC; `local` (sentence-transformers e5-small) — после установки VC++; ретраи с бэк-оффом на 429/5xx/таймаут |
| Векторное хранилище | `VECTOR_STORE=numpy` (портативный, без MSVC); `chroma` (ChromaDB) — после VC++ |
| **Hybrid RAG** (7.2) | `HYBRID_RAG=true`: векторный поиск + **BM25 (Okapi)** с fusion через **RRF** (обёртка `HybridVectorStore` над любым VectorStore, чистый Python без зависимостей) |
| **Адаптивная сложность** | `ADAPTIVE_BANDIT=true`: **LinUCB contextual bandit** (руки = сложности, контекст = мастерство темы/класс/недавний результат, награда = score оценки); `false` — эвристика «3 верных → ↑, 2 ошибки → ↓» |
| Судья (К-4) | Gemini на RouterAI (без VPN); OpenRouter для судьи не используется |
| Источники (К-2) | Только легальные: локальные PDF (Plan B), Викиучебник, открытые страницы; капчу не обходим |
| Сканы (3.2) | `detect_text_layer` → агент просит **страницы + тему** → OCR **только их** (`ocr_pages`, ru+en) с буфером `OCR_PAGE_BUFFER` и автодетекцией смещения номера; валидация темы; CPU-OCR медленный — поэтому только нужные страницы. **OCR-движок — EasyOCR** (лёгкий, без MSVC). PaddleOCR (точнее для русского, быстрее на CPU) отклонён: `paddlepaddle` ~500МБ и конфликтует с принципом портативности MVP; при необходимости замена — через абстракцию OCR-провайдера с нормализацией формата к `(bbox, text, conf)` |
| Дешёвые роли (В-2) | `CHEAP_MODEL=google/gemma-3-4b-it` на RouterAI; при отказе — fallback TUTOR_MODEL |
| **Quick answer toggle** (Фаза 1) | Popup settings ⚙ → quickAnswer: true = автоотправка мгновенно; false = полоса подтверждения «Вы выбрали X» |
| **Разделение busy** (Фаза 1) | uploadBusy (индексация) / chatBusy (квиз); Banner индексации не мешает квизу |
| **SQLite персистентность** (Фаза 2 🟡) | `SessionSQLiteStore` сохраняет состояние после каждого шага в `data/session_persist.db` |

## Структура

```
src/           config, llm_client, guardrails, nlp, intake, curriculum (ФГОС),
               knowledge (RAG), adaptive (LinUCB), source_finder (crawl4ai), tutor,
               judge, graph, metrics
api/           Pydantic-схемы (IntakeStatusResponse, QuizCard, MessageResponse, WsEvent)
tests/         341 тест
evals/         golden_set.json, intent_dataset.json, edututor_eval.py
main.py        CLI-демо
```
