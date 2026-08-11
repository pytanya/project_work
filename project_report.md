# Project Report — EduTutor (MVP)

Дата: 2026-08-10. Формат проектной работы: «схема + краткое описание» +
консольное демо (раздел 15.0 SPECIFICATION.md).

## 1. Что сделано

Реализован образовательный агент-тьютор **EduTutor** (MVP, разделы 1–7 и 12
спецификации-контракта):

- **Intake-фаза**: чек-лист, `validate_intake` (достаточность, лимит итераций,
  контроль прогресса В-3, экстренный старт), сверка темы с ФГОС (В-8).
- **NLP** (В-1): rule-based intent (quiz/explain/deep_dive/homework) + regex-NER
  (класс/предмет/автор/глава) + LLM-дополнение. Intent accuracy на датасете — **1.0** (порог ≥ 0.8, В-9).
- **Источник** (К-2, 6.2): fallback-цепочка — локальные PDF (Plan B) →
  Викиучебник/открытые страницы → `source_failed`. license_check, SSRF-защита,
  robots/rate-limit/кэш. crawl4ai (dynamic rendering) — без обхода капчи/ToS.
- **RAG**: Docling→pdfplumber, чанки «Параграф N», embeddings (RouterAI
  `multilingual-e5-large` или локальный e5-small), NumpyStore/ChromaDB, фильтры.
- **Тьюторинг** (7.1, Ж-3/Ж-6/Ж-8): генерация вопросов (дешёвая/тьютор-модель),
  пре-оценка простоты (rule-based→cheap→fallback, В-2), оценка (TUTOR основной,
  EXPERT — сложные ответы), `update_knowledge_map` (экспоненциальное сглаживание),
  адаптивная сложность, объяснение ошибок с цитатой §N, anti-repeat.
- **Судья** (К-4): три контракта (вопрос/объяснение/оценка), Gemini на RouterAI.
- **Граф** (LangGraph): условные рёбра route_source / route_textbook_result /
  route_tutor; checkpointer (AsyncSqliteSaver — опционально для API).
- **API-схемы** (В-4): `IntakeStatusResponse`, `QuizCard`, `MessageResponse`, `WsEvent`.
- **Evals** (В-6): `evals/edututor_eval.py` — intake_success, find_textbook_success,
  judge_score_*, intent_accuracy, cost_by_role, cheap_refusal_rate.

Тесты: **236** (юнит + интеграционные, в т.ч. реальные вызовы RouterAI).

## 2. Демо-результат (реальный прогон)

`python main.py --scenario schoolchild_grade6_geography --auto --questions 3`:

- Источник: локальный PDF «Алексеев, География 5–6» (Plan B) → pdfplumber → чанки.
- Вопросы сгенерированы по реальному содержанию учебника («Ты поднялся в горы на
  два километра…», «Самый нижний слой атмосферы?») с вариантами ответов.
- Оценка + объяснение + судья по каждому ответу.
- Стоимость сессии: **$0.0016** (tutor $0.0009, expert $0.0006, judge $0.0001).
- Карта знаний: `{'Атмосфера': 0.17}` (в демо авто-ответы короткие → пре-оценка).

## 3. Метрики (Этап 6)

| Метрика | Значение | Критерий |
|---------|----------|----------|
| intent_accuracy (В-9) | 1.0 | ≥ 0.8 |
| intake_success (golden set, mock) | 1.0 (3/3) | — |
| find_textbook_success (mock) | 1.0 | — |
| source_failed (сценарий no_materials) | ✓ | В-3 |
| judge_score_evaluation (mock) | 8.0 | ≥ 7 |
| cheap_refusal_rate (В-2) | 0.0 | — |
| Стоимость реальной сессии | $0.0016 | ≤ MAX_COST_USD |

## 4. Соответствие требованиям курса (7 вопросов)

1. **Полезная задача**: адаптивный тьюторинг для школьников N-го класса и
   студентов: авто-поиск учебника в легальных источниках → разбор → квизы →
   объяснение ошибок → карта знаний.
2. **Самостоятельный выбор действия**: LangGraph — условные рёбра `route_source`,
   `route_textbook_result`, `route_tutor`; после оценки ответа агент решает:
   следующий вопрос / объяснение ошибки / смена сложности / завершение.
3. **Выбор модели**: дешёвая (`google/gemma-3-4b-it`) — простые вопросы и пре-оценка;
   тьютор (`qwen3.7-flash`) — генерация/оценка в основном потоке; эксперт
   (`deepseek-v4-flash`) — развёрнутые/сложные ответы (Ж-8); судья (Gemini на
   RouterAI) — 3 контракта качества (К-4).
4. **Function calling**: `rag_search`, `search_web`, `fetch_url`, `fetch_html`,
   `crawl_page_js`, `download_file`, `process_document`, `classify_intent`,
   `extract_entities`, `save_progress` — реестр инструментов (`tools`-модуль расширен).
5. **Обращение к памяти**: перед генерацией каждого вопроса — RAG-поиск по
   хранилищу (фильтр subject/grade/section) + приоритет слабых тем из
   `knowledge_map` (Ж-6).
6. **Ветвление/повтор/остановка**: ветвление — intake-валидация, маршруты источника,
   source_failed; повтор — адаптивная сложность, цикл уточнений (≤8 итераций,
   ≤2 без прогресса → экстренный старт), anti-repeat; остановка —
   `MAX_QUESTIONS_PER_SESSION`, `MAX_LLM_CALLS_PER_SESSION`, бюджеты по ролям (В-7).
7. **Доказательство работы**: 236 тестов, EduTutorEval (golden set + intent-датасет),
   3 контракта LLM-as-Judge, метрики стоимости по ролям, демо-прогон (см. выше).

## 5. Компромиссы и ограничения

1. **Судья (К-4)**: реального второго провайдера нет — судья = Gemini на RouterAI
   (тот же шлюз). Риск self-evaluation bias зафиксирован; требуется ручная валидация
   (Ж-6) на этапе приёмки.
2. **Блокировка OpenRouter в РФ**: fallback для тьютора/эксперта работает только
   под VPN; судья работает без VPN (RouterAI).
3. **Embeddings**: по умолчанию `EMBEDDING_PROVIDER=api` (RouterAI) — без локального
   torch/VC++; локальный вариант (sentence-transformers e5-small) доступен после
   установки Microsoft VC++ Redistributable.
4. **Векторное хранилище**: по умолчанию `VECTOR_STORE=numpy` (портативный);
   ChromaDB (спецификация) — после установки VC++ (rust-binding).
5. **Источники (К-2)**: полный PDF учебника легально недоступен — агент собирает
   материалы по теме из Plan A; Plan B — локальные PDF из Downloads (приёмка).
6. **crawl4ai**: версия 0.9.2 (PyPI); капчу/аутентификацию/ToS не обходим (К-2).
7. **Yandex GPT**: запасной РФ-провайдер — не подключался (нет ключа, Ж-7).

## 6. Дефекты спецификации, найденные при реализации

1. **`MAX_INTAKE_ITERATIONS=3` (раздел 14) ломает полный чек-лист.** Чек-лист — 5–6
   вопросов; лимит 3 исчерпывается на 4-м ответе и уводит в экстренный старт до
   завершения intake. **Решение**: прагматично поднят до 8 (чек-лист + запас на
   уточнения); экстренный старт по «не знаю» управляется streak'ом (В-3).
2. **`crawl4ai` написан как «craw4ai»** в спецификации, requirements и коде — исправлено.
3. **`eval_golden.py` клона** не адаптируется (В-6) — написан новый `edututor_eval.py`.
4. **sentence-transformers/chromadb требуют MSVC** — зафиксированы портативные
   альтернативы (см. раздел 5).

## 7. Как воспроизвести

```bash
pip install -r requirements.txt
copy .env.example .env   # ROUTERAI_API_KEY
python -m pytest tests/ -v
python evals/edututor_eval.py --runs 3
python main.py --scenario schoolchild_grade6_geography
```
