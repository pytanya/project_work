# evals/ — результаты и воспроизведение Этапа 0 (доступность моделей)

Этап 0 проекта EduTutor проверяет доступность моделей через тест-колл
[`scripts/model_probe.py`](../scripts/model_probe.py) (OpenAI-совместимые endpoints).

## Как воспроизвести результаты

```bash
python scripts/model_probe.py
```

Требования:
- Python 3 + пакет `requests` (окружение собирается на Этапе 0);
- файл `.env` в корне проекта с ключами:
  - `ROUTERAI_API_KEY` — обязателен (все модели, включая судью Gemini, идут через RouterAI);
  - `ROUTERAI_BASE_URL` — опционально (по умолчанию `https://routerai.ru/api/v1`);
  - `YANDEX_API_KEY` + `YANDEX_FOLDER_ID` — опционально (тест-колл Yandex Search API).

Провайдеры моделей (Этап 0, раздел 14 SPECIFICATION.md):
- `TUTOR_MODEL` (`qwen/qwen3.7-flash`) — **RouterAI**;
- `EXPERT_MODEL` (`deepseek/deepseek-v4-flash`) — **RouterAI**;
- `CHEAP_MODEL` (`google/gemma-3-4b-it`) — **RouterAI** (модель подтверждена на Этапе 0; `qwen/qwen2.5-flash` на RouterAI не существует — 400);
- `JUDGE_MODEL` (`google/gemini-3.5-flash-lite`) + fallback (`google/gemini-3.1-flash-lite`) — **RouterAI, без VPN** (OpenRouter для судьи не используется).

## Результаты

- [`evals/model_probe_results.json`](model_probe_results.json) — **реальные результаты** прогона (исторический прогон от 2026-08-08).
- [`evals/model_probe_results.example.json`](model_probe_results.example.json) — шаблон структуры результата.

Результаты Этапа 0 **коммитятся в репозиторий** (исключение добавлено в `.gitignore`,
`!evals/model_probe_results*.json`) — они воспроизводимы и сохраняются.

> ВАЖНО: реальный прогон требует API-ключей заказчика и должен быть выполнен
> на этапе исполнения. Без ключей скрипт выведет `[SKIP]`/`ERR` и вернёт код 1.
