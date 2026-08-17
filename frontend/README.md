# EduTutor Frontend (React + Vite)

UI расширения заказчика (раздел 9): IntakeWizard, QuizCard, ExplanationPanel,
SourceSearchPanel, FileUpload, ProgressDashboard, ChatStream.

## Запуск

```bash
# 1. Бэкенд (FastAPI, порт 8000)
uvicorn api.app:app --host 0.0.0.0 --port 8000

# 2. Фронтенд (порт 5173; /api проксируется на :8000)
npm install
npm run dev
```

Открыть http://localhost:5173

## Сборка

```bash
npm run build        # → dist/
npm run preview      # превью собранного билда
```

## Тесты

```bash
npm test             # Vitest: 27 тестов (компоненты + App, jsdom)
npm run e2e          # Playwright (chromium): 8 e2e против живого бэкенда (автостарт uvicorn+vite)
```
