# EduTutor — Инструкция по запуску

## Быстрый старт

### 1. Активируй виртуальное окружение

```bash
cd C:\otus\project_work
.venv\Scripts\Activate.ps1
```

### 2. Запусти бэкенд (в терминале 1)

```bash
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
```

Или используй автозапуск:
```bash
python run_server.py
```

### 3. Запусти фронтенд (в терминале 2)

```bash
cd frontend
npm run dev
```

### 4. Открой в браузере

- **UI:** http://localhost:5173
- **API Docs:** http://localhost:8000/docs
- **Health:** http://localhost:8000/api/health

---

## Что работает

### ✅ Фаза 1 — UI/UX (завершено)

| Функция | Статус | Как проверить |
|---------|--------|---------------|
| Шестерёнка настроек ⚙ | ✅ | Клик на ⚙ рядом с брендом |
| Toggle "Быстрый ответ" | ✅ | В popup settings: true=авто, false=подтверждение |
| Разделение busy | ✅ | Баннер "Загрузка" не мешает квизу |
| Typing Indicator | ✅ | Три зелёные точки при ожидании ответа |
| Счётчик Вопрос N/M | ✅ | В QuizCard справа: "вопрос 3/10" |
| Фокус в поле ввода | ✅ | Возвращается после отправки |

### ✅ Фаза 2 — Backend (80%)

| Функция | Статус | Как проверить |
|---------|--------|---------------|
| Автоэкспорт CSV+OKF | ✅ | После завершения квиза в output/ |
| SQLite persistence | 🟡 | Сессии сохраняются в data/session_persist.db |
| Восстановление сессий | 🟡 | При перезапуске сервера прогресс сохраняется |
| Предгенерация вопросов | ⬜ | Не начато (single-step invoke) |

### ✅ Исправленные баги

| Баг | Решение | Как проверить |
|-----|---------|---------------|
| "Раздумия" после ответа | busy сбрасывается при WS событии | Клик по варианту → мгновенная проверка |
| Пустой экран после темы | UI не скрывается, ждём WS | Клик по теме → "Готовимся по теме: X..." |
| 500 error на /topic | Added try/except, logging | Клик по теме → без ошибок |

---

## Тестирование

### Python tests (backend)

```bash
.venv/Scripts/python.exe -m pytest tests/test_graph.py -v
# 15 passed ✅

.venv/Scripts/python.exe -m pytest tests/test_tutor.py -v
# 30 passed ✅
```

### Playwright tests (frontend)

```bash
cd frontend
npx playwright test e2e/app.spec.js --reporter=list
# 2 passed ✅
```

### Консольное демо

```bash
python main.py --scenario schoolchild_grade6_geography --auto --questions 3
```

---

## Известные проблемы

### 1. SQLite не инициализируется

**Симптомы:**
```
WARNING: Не удалось инициализировать SQLite — отключаем персистентность
```

**Причина:**
- Путь `data/sources_cache/../session_persist.db` не создаётся
- Или нет прав доступа

**Решение:**
```bash
mkdir -p data/sources_cache
```

### 2. Full flow test требует реальный учебник

**Симптомы:**
```
Error: expect(locator).toBeVisible() failed
Locator: getByText(/Изучаем:/).first()
```

**Причина:**
- Нет проиндексированного учебника
- Knowledge graph пустой

**Решение:**
- Загрузи PDF учебник через UI
- Или добавь mock данные для тестов

### 3. Topic selection UI не показывает "Изучаем:"

**Симптомы:**
- После клика по теме нет сообщения "Изучаем: X"

**Причина:**
- `active_topic` не передаётся в KnowledgeGraphPanel

**Решение:**
- Проверь что `setGraph` вызывается с `activeTopic: r.active_topic`

---

## Структура проекта

```
C:\otus\project_work/
├── api/                    # FastAPI backend
│   ├── routes/            # Endpoints (sessions, messages, graph)
│   ├── engine.py          # SessionStore, run_step
│   └── app.py             # FastAPI application
├── src/                   # Core logic
│   ├── graph.py           # LangGraph state machine
│   ├── tutor.py           # Quiz generation, evaluation
│   ├── session_store.py   # SQLite persistence
│   └── states.py          # Pydantic models
├── frontend/              # React UI
│   ├── src/
│   │   ├── App.jsx        # Main component
│   │   ├── components/    # UI components
│   │   └── tests/e2e/     # Playwright tests
│   └── e2e/               # E2E test specs
├── tests/                 # Unit tests
├── data/                  # Persisted data
│   ├── session_persist.db # SQLite (if enabled)
│   └── chroma/            # Vector store
└── output/                # Exported reports
```

---

## Команды для отладки

```bash
# Посмотреть логи бэкенда
tail -f logs/*.log

# Проверить API health
curl http://localhost:8000/api/health

# Проверить сессии
curl http://localhost:8000/api/sessions

# Посмотреть SQLite
sqlite3 data/session_persist.db ".tables"
sqlite3 data/session_persist.db "SELECT * FROM sessions LIMIT 5;"
```

---

## Следующие шаги

### Близкий план (следующая сессия)

1. **Добавить mock knowledge graph** — для тестирования без реального учебника
2. **Debug topic-gate.spec.js** — worker process crashed
3. **E2E test для quick answer toggle** — проверка localStorage

### Среднесрочный план

4. **Batch question generation** — pool 3-5 вопросов за 1 LLM call
5. **Визуальная обратная связь** — green/red highlight на вариантах
6. **Progress bar анимация** — CSS transitions

---

## Статус

**MVP функциональность:** ✅ Работает
**UI оптимизации:** ✅ Завершены
**Backend persistence:** 🟡 Интегрирован
**E2E тесты:** ✅ Basic flow passes

**Продукт готов к тестированию пользователем!**
