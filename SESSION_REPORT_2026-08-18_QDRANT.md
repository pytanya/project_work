# Сессия 2026-08-18 — Roadmap #1: Qdrant векторное хранилище

## Решение

Из четырёх пунктов `roadmap.md` первым реализован пункт **#1 «Qdrant в Docker вместо
in-memory VectorStore»**. Это базисная инфраструктура (persistent vector search,
metadata-фильтрация), на которую опираются пункты 2–4 (Wiki-LLM, граф знаний,
источники).

Ключевая идея: адаптер сделан **двухрежимным**, поэтому разработка и тесты не
заблокированы отсутствием Docker:

| Режим | Настройка | Когда нужен |
|-------|-----------|-------------|
| **server** | `QDRANT_URL=http://localhost:6333` | Production / `docker compose up -d qdrant` |
| **embedded** | `QDRANT_PATH=./data/qdrant` | Локальная персистентная БД **без Docker и без виртуализации** |

Embedded-режим использует встроенное локальное хранилище `qdrant-client`
(`QdrantClient(path=...)`) — полноценный персистентный векторный поиск на чистом
Python, не требующий ни сервера, ни Docker Desktop, ни Hyper-V/WSL2.

## Что сделано (файлы)

### Новые файлы
- `src/qdrant_store.py` — `QdrantStore` (add/search/count/reset/delete), payload-поля
  `subject/grade/section_number/section_title/source/page_number` для
  metadata-фильтрации, авто-создание коллекции при старте (миграция), два режима.
- `tests/test_qdrant_store.py` — 9 тестов: add/search/filter, пустое хранилище,
  reset, **persistence между инстансами** (embedded), delete по ids, детерминизм
  uuid, фабрики `make_store`/`make_qdrant_store`, неизвестный backend.
- `docker-compose.yml` — qdrant (6333/6334) + backend (FastAPI) + frontend (Vite).

### Изменённые файлы
- `src/knowledge.py` — `make_store()` принимает `backend="qdrant"`; новые
  `make_qdrant_store()` и `_model_dimension()` (статическая карта размерности
  эмбеддинга — без лишнего сетевого вызова); рефакторинг `make_collection_name()`.
- `src/config.py` — настройки `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_PATH`
  (+ валидатор путей).
- `api/app.py` — `GET /api/health` возвращает активный векторный бэкенд
  (`vector_store` + `collection`); для `HybridVectorStore` отдаёт внутренний класс.
- `requirements.txt` — добавлен `qdrant-client`.
- `.env.example` — блок Qdrant с комментариями по режимам.
- `tests/test_api.py` — `TestHealthMetrics.test_health` обновлён под новые поля.

### Документация
- `SPECIFICATION.md` — раздел 3.3 «Хранение и поиск» (Qdrant server/embedded,
  payload-поля, миграция), раздел 14 (конфиг QDRANT_*), заголовок-лог.
- `README.md` — секция «Qdrant векторное хранилище», таблица решений, структура.
- `roadmap.md` — пункт #1 отмечен ✅ (адаптер готов и протестирован).
- `project_report.md` — компромисс 4 (векторное хранилище) дополнен Qdrant.
- `AUTONOMOUS_WORK_REPORT.md` — добавлен блок сессии.

## Верификация

| Набор | Результат |
|-------|-----------|
| `pytest tests/test_qdrant_store.py` | 9 passed |
| `pytest` (значимые наборы, без сети/OCR) | ~75 passed / 1 skipped, регрессий нет |
| Vitest (frontend) | 27 passed (9 файлов) |
| Playwright chromium | `app.spec` 2, `session-speed` 1, `topic-flow` 2, `topic-gate` 1 — все passed (topic-gate ранее падал) |
| Smoke `GET /api/health` | `vector_store: QdrantStore` (embedded) |
| `make_graph_deps` с `VECTOR_STORE=qdrant` | HybridVectorStore → QdrantStore, count 0 |

## Как включить

```env
# .env
VECTOR_STORE=qdrant
QDRANT_URL=http://localhost:6333     # server-режим (docker compose up -d qdrant)
# QDRANT_PATH=./data/qdrant          # embedded-режим (без Docker)
```

## Заметка по Docker Desktop (виртуализация)

Docker Desktop для Windows требует гипервизор: **WSL2 backend** (Virtual Machine
Platform) или **Hyper-V**. Ошибка «виртуализация не поддерживается» обычно означает:

1. VT-x/AMD-V **выключен в BIOS/UEFI** — включить (Intel VT-x / AMD SVM), перезагрузиться.
2. Машина сама **виртуализирована** (VirtualBox/VMware/облако) — нужно включить
   **nested virtualization** у гипервизора-хоста.
3. На старых CPU без VT-x/AMD-V аппаратная виртуализация недоступна.

**Диагностика на этой машине (systeminfo / Get-ComputerInfo):**

```
HyperVisorPresent                                 : False
HyperVRequirementDataExecutionPreventionAvailable : True
HyperVRequirementSecondLevelAddressTranslation    : True
HyperVRequirementVirtualizationFirmwareEnabled    : False   ← ПРИЧИНА
HyperVRequirementVMMonitorModeExtensions          : True
```

Ключевая строка — `HyperVRequirementVirtualizationFirmwareEnabled : False`:
аппаратная виртуализация **выключена в BIOS/UEFI**. Остальные требования (SLAT,
DEP, VMM-расширения) в порядке. Программно это не включить — только через BIOS.

**Практический вывод для проекта:** Docker/Qdrant-сервер не обязателен — для
локальной разработки, демо и сдачи достаточно **embedded-режима**:
`VECTOR_STORE=qdrant` + `QDRANT_PATH=./data/qdrant`. Он персистентен, не требует
ни Docker, ни виртуализации. Server-режим понадобится только для production-развёртывания.

### Итоговое состояние после перезапуска (2026-08-18)

Старые процессы (uvicorn 8000, vite 5173) держали прежний конфиг
(`VECTOR_STORE=chroma`). Процессы убиты, конфиг переключён, сервер перезапущен:

```env
VECTOR_STORE=qdrant
QDRANT_PATH=./data/qdrant     # embedded, без Docker/виртуализации
```

Проверка:

| Сервис | Статус |
|--------|--------|
| Backend `GET /api/health` | `{"status":"ok","vector_store":"QdrantStore","collection":"edututor_1024"}` |
| Frontend `http://localhost:5173` | HTTP 200 |

## Следующий кандидат

Пункт **#2 «Wiki-LLM (Karpathy-стиль) + OKF persistent knowledge»**: агент не просто
проверяет знания, а накапливает их между сессиями (wiki-статьи с YAML-frontmatter,
обновление по ошибкам ученика, персистентность через SQLite + OKF-бандлы).
Надстраивается поверх уже работающих: OKF-экспорт (`emit_okf_bundle`),
SQLite-персистентность сессий, Qdrant-хранилище.
