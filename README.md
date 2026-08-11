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

## Установка (Windows)

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Браузер для crawl4ai (dynamic rendering, расширение заказчика)
python -m playwright install chromium

# Конфигурация
copy .env.example .env        # заполнить ROUTERAI_API_KEY
```

> **MSVC Redistributable** нужен для `sentence-transformers` (torch) и ChromaDB
> (rust). Без него работают альтернативы (по умолчанию):
> `EMBEDDING_PROVIDER=api` (RouterAI /embeddings) и `VECTOR_STORE=numpy`.
> Скачать: https://aka.ms/vs/17/release/vc_redist.x64.exe

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
