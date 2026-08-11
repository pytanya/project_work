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

REST: сессии, intake, upload, find-textbook, message, cancel, history, health, metrics.
WS `/api/sessions/{id}/ws`: `intake.question`, `source.progress`, `quiz.card`,
`tutor.explanation`, `tutor.summary`, `source.failed`.

## Тесты и eval

```bash
python -m pytest tests/ -v              # 236 тестов (юнит + интеграционные RouterAI)
python evals/edututor_eval.py --runs 3  # EduTutorEval: intake/find_textbook/judge + intent accuracy
python evals/edututor_eval.py --mock    # офлайн-режим
```

## Ключевые решения и компромиссы

| Вопрос | Решение |
|--------|---------|
| Embeddings | `EMBEDDING_PROVIDER=api` (RouterAI `intfloat/multilingual-e5-large`) — без локального torch/MSVC; `local` (sentence-transformers e5-small) — после установки VC++ |
| Векторное хранилище | `VECTOR_STORE=numpy` (портативный, без MSVC); `chroma` (ChromaDB) — после VC++ |
| Судья (К-4) | Gemini на RouterAI (без VPN); OpenRouter для судьи не используется |
| Источники (К-2) | Только легальные: локальные PDF (Plan B), Викиучебник, открытые страницы; капчу не обходим |
| Дешёвые роли (В-2) | `CHEAP_MODEL=google/gemma-3-4b-it` на RouterAI; при отказе — fallback TUTOR_MODEL |

## Структура

```
src/           config, llm_client, guardrails, nlp, intake, curriculum (ФГОС),
               knowledge (RAG), source_finder (crawl4ai), tutor, judge, graph, metrics
api/           Pydantic-схемы (IntakeStatusResponse, QuizCard, MessageResponse, WsEvent)
tests/         236 тестов
evals/         golden_set.json, intent_dataset.json, edututor_eval.py
main.py        CLI-демо
```
