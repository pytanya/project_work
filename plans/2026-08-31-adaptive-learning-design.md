# Дизайн: адаптивное обучение — Student KG v2 + Scaffolding + Spaced Repetition

> Дата: 2026-08-31. Статус: черновик спеки (проверен автором, ждёт ревью).
> Контекст: roadmap #4 (Student Knowledge Graph) — довести и полностью встроить;
> новые подсистемы: адаптивное усложнение (scaffolding) и интервальное повторение
> (SM-2 Question Bank). Архитектурные решения переняты из DeepTutor (HKUDS/DeepTutor:
> mastery gate в Guided Learning, Question Bank, трёхуровневая память) и LlamaTutor
> (Nutlope/llamatutor: динамический curriculum = intake + выбор темы + рекомендации).

## 1. Цель и принцип

Три подсистемы работают как единый контур «ошибка → знание → повторение»:

1. **Student Knowledge Graph (довести, roadmap #4)** — персистентный профиль тем
   (статус `not_studied/in_progress/mastered`, mastery, weak_areas, relations) —
   источник правды для адаптации между сессиями.
2. **Scaffolding** — при ошибке студента агент не даёт сразу правильный ответ,
   а спускается по лестнице подсказок и при необходимости декомпозирует задачу.
3. **Spaced Repetition** — ошибочные вопросы копятся в Question Bank (SM-2);
   по явному запросу студента агент гоняет блиц-опрос по должным карточкам.

**Архитектурный принцип:** сохраняем гибрид проекта — детерминированная FSM
(дефолтный путь, дёшево, тестируемо) + агентный цикл `USE_AGENT_TUTOR` как опция.
Логика реализуется в детерминированном слое; агентный цикл получает тонкие
инструменты-обёртки (тот же паттерн, что `evaluate_and_record` в `src/evaluation.py`
уже объединяет узел и инструмент `evaluate_answer`).

**Императив надёжности:** у каждого LLM-вызова есть детерминированный fallback;
любой сбой подсистемы адаптации не ломает текущий квиз (fail-soft). Флаги
`ENABLE_SCAFFOLDING` / `ENABLE_SPACED_REPETITION` (по умолчанию `true`) возвращают
поведение к текущему при `false`.

## 2. Текущее состояние и найденные дефекты

Референс-отчёты (23–24-я итерации разбора) зафиксировали фактическое состояние:

**Уже есть:**
- `src/student_kg.py` — `StudentKnowledgeGraph` (статусы, mastery, weak_areas,
  relations, рекомендации), `StudentKnowledgeGraphStore`, API в `api/routes/students.py`
  (`GET/POST .../knowledge-graph`, `GET .../recommendations`).
- `StudentProfile.knowledge_graph` (JSON `data/students/<sid>.json`).
- `src/adaptive.py` — LinUCB bandit выбора сложности (`ADAPTIVE_BANDIT=true`).
- `records` в `TutorState` — полнота данных по каждому вопросу
  (`question`, `options`, `correct_answer`, `student_answer`, `score01`, `correct`,
  `difficulty`, `topic`).
- WS-события `quiz.card`, `tutor.explanation`, `tutor.summary`, `system`.
- Фронтенд-панель `StudentKGPanel` («Мои знания»).

**Дефекты, которые чиним в этой работе:**
- `src/graph.py:1321` и `:1875`: `StudentKnowledgeGraphStore()` создаётся **без**
  `student_store` → `get()` всегда `None`, `save()` — no-op; вдобавок вызывается
  **несуществующий** метод `update_knowledge_graph` (есть только `update_batch` на
  `StudentKnowledgeGraphStore` и `update_knowledge_graph` на `StudentStore`).
  → интеграция в граф фактически мёртвый код.
- `src/graph.py:1859–1880`: переменная `wiki` в `summary_node` может уйти в `NameError`.
- `src/student_kg.py:140–146`: `set_topic` всегда пересчитывает `status` и игнорирует
  переданный статус → `mark_in_progress` с `attempts=0` даёт `not_studied`
  (статус «в процессе» теряется).
- `TutorState`/`QuizCard` не имеют полей подсказок и декомпозиции; WS-события
  `quiz.hint`/`review.*` отсутствуют; `explain_error` вызывается только для открытых
  вопросов (`src/evaluation.py:131–138`).

## 3. Модель данных

### 3.1. `TutorState` (дополнить `src/states.py`)

```python
hint_level: int = 0                # уровень выданной подсказки по текущему вопросу (0..MAX_HINTS)
attempts_on_question: int = 0      # попыток по текущему вопросу (вкл. повторные после подсказок)
retry_question_id: Optional[str] = None   # вопрос, по которому идёт повторная попытка после подсказки
subtask_queue: Optional[List[str]] = None # очередь подзадач декомпозиции (пошаговый разбор)
review_requested: bool = False     # запрошен блиц-опрос (команда/кнопка/инструмент)
review_active: bool = False        # идёт блиц-опрос
review_cards: List[Dict[str, Any]] = []   # должные карточки, выбранные для блица
review_index: int = 0              # текущая карточка блица
```

### 3.2. `QuizCard` (дополнить `api/schemas.py` и генерацию `src/tutor.py`)

```python
hint: Optional[str] = None
subtasks: Optional[List[str]] = None   # 1–3 шага для многошаговых открытых задач
```

### 3.3. `ReviewCard` (новый модуль `src/review.py`)

```python
class ReviewCard(BaseModel):
    card_id: str          # хэш вопроса (устойчивый к дублям)
    student_id: str
    subject: str = ""
    topic: str = ""
    question: str
    options: Optional[List[str]] = None
    answer_type: str = "open"
    correct_answer: str = ""       # эталон (current_answers), для закрытых — ответ
    difficulty: str = "medium"
    added_at: str = ""             # ISO
    last_reviewed: str = ""        # ISO
    due_at: str = ""               # ISO — дата следующего повтора
    interval_days: float = 1.0
    ease: float = 2.5              # SM-2 фактор лёгкости, min 1.3
    reps: int = 0                  # успешных повторов подряд
    lapses: int = 0                # срывов (забываний)
```

Хранилище: `data/review_bank/<student_id>.json` (конфиг `REVIEW_BANK_DIR`).
Потокобезопасная запись: tmp + rename (как `StudentStore.save`).

## 4. Подсистема 1 — Student Knowledge Graph: доведение и интеграция

### 4.1. Починка интеграции (Фаза A)
- В `GraphDeps` (`src/graph.py:305`) добавить `student_store: Optional[Any] = None`.
- В `make_graph_deps` инжектировать `StudentStore(root_dir=s.STUDENTS_DIR)`.
- В `content_node` (`graph.py:1315`) и `summary_node` (`graph.py:1869`) использовать
  `deps.student_store or StudentStore(root_dir=deps.settings.STUDENTS_DIR)` и вызывать
  существующий `StudentStore.update_knowledge_graph(...)` (метод `src/student.py:166`).
- Убрать риск `NameError` в `summary_node` (инициализировать `wiki` до try).

### 4.2. Починка статусов (`src/student_kg.py`)
- `set_topic`: уважать явно переданный `status` (не перетирать при `attempts==0`),
  авто-мастеринг оставить для случая «статус не задан».
- `mark_in_progress`: корректно ставить `in_progress`.
- `mark_mastered` / авто-переход `mastered` (attempts≥3 и mastery≥0.8) — как есть.

### 4.3. Живые апдейты на каждый ответ (`src/evaluation.py`)
В `evaluate_and_record`, рядом с `wiki.apply_record`, добавить идемпотентный синк темы
в Student KG: `attempts`/`correct` по всем records темы, `mastery = knowledge_map[topic]`,
`weak_areas` из `graded.feedback` при ошибке. Хелпер `sync_student_kg(st, deps, card)`.

### 4.4. Relations из графа учебника
При готовности `st.knowledge_graph` (после выбора темы) синхронизировать рёбра
`prerequisite`/`related` в Student KG (тема ↔ соседи по графу) — хелпер
`sync_relations_from_textbook_graph(st, kg)`.

### 4.5. Mastery-гейт (DeepTutor Guided Learning)
- В `topic_gate_node`/`POST /topic` (`api/routes/graph.py:183`): если у выбранной темы
  есть `get_prerequisite_gaps(...)` и они не `mastered` → WS-событие `system`
  (`kind="mastery.gate"`): «Прежде чем тема X, стоит повторить Y», с кнопками
  «Всё равно продолжить» / «Перейти к Y». Жёсткой блокировки нет — студент свободен.
- Контекст агента: добавить сводку Student KG (статусы тем, слабые места,
  рекомендации) в `_tutor_context` (`src/agent_loop.py`) и в system-промпт intake.

### 4.6. Результат Фазы A
- Статусы тем переживают сессии и обновляются: урок → `in_progress`; каждый ответ →
  attempts/correct/mastery; завершение квиза → `mastered`/слабые места; пререквизиты →
  из графа учебника; mastery-гейт поверх.

## 5. Подсистема 2 — Scaffolding: лестница подсказок + декомпозиция

### 5.1. Модуль `src/scaffold.py`
```python
MAX_HINTS = 2                 # конфиг MAX_HINTS_PER_QUESTION
MAX_ATTEMPTS = 3              # 1 исходная + 2 повторные

def hint_for(question, correct_answer, context, level, state, llm_call) -> str:
    # level 1: наводящая (термин/направление), не раскрывает ответ
    # level 2: раскрывающая («начни так: …», ключевая идея)
    # LLM-вызов дешёвой ролью; FALLBACK (LLM недоступен/пусто) — rule-based:
    #   level1 → «Вспомни: <первый ключевой термин correct_answer>»
    #   level2 → «Правильный ответ начинается так: <первые слова correct_answer>»

def make_subtasks(question, answer_type, context, llm_call) -> Optional[List[str]]:
    # для многошаговых открытых задач — 1–3 шага; FALLBACK: None → без декомпозиции
```

### 5.2. Поток в графе (детерминированный цикл)
- `generate_question`: промпт дополнить полями `hint` и (для open/многошаговых)
  `subtasks`; заполнять `QuizCard.hint/subtasks`.
- `evaluate_answer` (в `evaluate_and_record`):
  - если `retry_question_id == card.question_id` (повтор после подсказки):
    - **верно** → финализируем record (score, answered_count++, knowledge_map, bandit),
      сбрасываем `hint_level/attempts_on_question/retry_question_id`, выводим похвалу;
    - **неверно** и `hint_level < MAX_HINTS` и `attempts_on_question < MAX_ATTEMPTS` →
      **НЕ финализируем** record, `hint_level += 1`, `attempts_on_question += 1`,
      сохраняем `current_question` и `pending_answer=None`, эмитим `quiz.hint`
      (hint, level, attempts_left), `route_tutor` → снова ждём ответ;
    - **неверно** и подсказки исчерпаны → финализируем record со score как есть,
      затем: если `subtasks` есть → `subtask_queue = card.subtasks`, иначе →
      `explain_error` как сегодня;
  - обычный первый ответ (не retry): как сегодня, но при неверном и
    `hint_level < MAX_HINTS` → **не финализируем** сразу: ставим `retry_question_id`,
    `hint_level=1`, эмитим `quiz.hint`, ждём повторную попытку (идентично пункту выше).
- Новый узел/маршрут **декомпозиции**: когда `subtask_queue` непуст и нет
  `current_question` — эмитим следующий шаг как `quiz.hint` с `kind="subtask"`
  (лёгкая проверка ответа студента: непустой ответ + keyword-совпадение с контекстом
  или просто подтверждение «понял»), после последнего шага — заново задаём исходный
  вопрос (`current_question` сбрасываем, `hint_level=0`).
- Защита: лимит подсказок/попыток; счёт `attempts_on_question` не влияет на
  `answered_count`/bandit (reward учитывается один раз за вопрос).

### 5.3. Агентный цикл (`USE_AGENT_TUTOR`)
- Инструмент `give_hint` (обёртка над `scaffold.hint_for`) + `submit_review` (см. п.6).
- В `TUTOR_AGENT_PROMPT` добавить политику scaffolding (сперва подсказка, не ответ).

### 5.4. WS и фронтенд (минимум)
- Новый тип события `quiz.hint`: `{question_id, hint, level, attempts_left, subtask?}`.
- `frontend/src/components/QuizCard.jsx`: поле `hint` (показать после ошибки / кнопка
  «Подсказка»), рендер `quiz.hint` как hint-карточку в `ChatStream`.
- Кнопка «Подсказка» в карточке (при `!quickAnswer`) отправляет `sendMessage('подсказка')`.

## 6. Подсистема 3 — Spaced Repetition (SM-2 Question Bank)

### 6.1. Модуль `src/review.py`
- `ReviewBank`:
  - `add_from_record(student_id, record)` — при ошибке в квизе; upsert по
    `card_id = sha256(question)` (обновляем тему/эталон/дату, не плодим дубли);
    лимит банка `REVIEW_BANK_MAX_CARDS` (по умолчанию 200, старые вытесняются).
  - `get_due(student_id, subject=None, limit=REVIEW_QUIZ_SIZE)` — карточки
    `due_at <= now`, сортировка по `due_at`; FALLBACK: банк отсутствует/битый → `[]`.
  - `review_card(card, correct)` — SM-2:
    - верно: `reps += 1`; `interval_days = (reps==1) ? 1.0 : round(interval_days*ease,1)`
      (при первом повторе → 1 день; идём к «3 дня назад» естественно через 2–3 повтора);
      `ease = max(1.3, ease + (0.1 - (3 - reps)*0.05))` — упрощённая вариация SM-2;
    - неверно: `reps = 0`, `interval_days = 1.0`, `lapses += 1`,
      `ease = max(1.3, ease - 0.2)`;
    - `due_at = now + interval_days`.
  - `stats(student_id)` — `{due, total, lapses, by_topic: {...}}`.

### 6.2. Поток в графе (детерминированный, «на рельсах» квиза)
- **Запись карточек:** в `evaluate_and_record` при `not correct` → `add_from_record`.
- **Запуск:** команда «повтори»/«закрепить»/«review» (CLI, message, кнопка) или
  `POST /api/sessions/{id}/review` → `state.review_requested=True` → после intake
  (или сразу, если данные есть) в `generate_question_node`:
  - если `review_active` и `review_index < len(review_cards)` → следующая карточка
    (эмит `quiz.card` с `review=true`, `question_num` = «Повторение N/M»);
  - если `review_requested` и карточки не загружены → `get_due(...)`, если пусто →
    `system` «Карточек на повторение нет», `review_requested=False`, обычный поток;
  - если очередь исчерпана → `review_active=False`, эмит `review.done`
    `{reviewed, correct, lapses}`, дальше обычный поток.
- **Оценка карточек:** `evaluate_answer` на карточке идёт штатно (для закрытых —
  детерминированно); после оценки карточки применяем `review_card(card, correct)`,
  обновляем Student KG темы (`last_studied`, при верном — небольшой бонус к mastery),
  `review_index += 1`.

### 6.3. Агентный цикл
- Инструмент `start_review` (взять должные карточки и запустить блиц) — обёртка над
  тем же сервисом; `submit_review` (оценка карточки + SM-2).

### 6.4. API и фронтенд (минимум)
- `GET /api/students/{id}/review` → `{due, stats}` (для бейджа/кнопки).
- `POST /api/sessions/{id}/review` → `{ok, due_count}` + фоновый `run_step`.
- CLI: команда `повтори` в основном цикле `main.py`.
- Фронтенд: кнопка «Повторить» в `StudentKGPanel`/шапке чата (видна при `due>0`),
  рендер `review.card`/`review.done` в `ChatStream`.

## 7. Конфигурация (`src/config.py`)

```python
ENABLE_SCAFFOLDING: bool = True
ENABLE_SPACED_REPETITION: bool = True
REVIEW_BANK_DIR: Path = BASE_DIR / "data" / "review_bank"
MAX_HINTS_PER_QUESTION: int = 2
REVIEW_QUIZ_SIZE: int = 5
REVIEW_BANK_MAX_CARDS: int = 200
```

## 8. Фолбэки и отказоустойчивость (запрос заказчика)

| Сбой | Поведение |
|---|---|
| LLM подсказки недоступен/пусто | rule-based из `correct_answer`/контекста |
| LLM не дал `subtasks` | декомпозиция не запускается → обычный `explain_error` |
| Банк карточек отсутствует/битый | пересоздать пустой; `get_due` → `[]` |
| Нет должных карточек | `system`: «повторять нечего»; обычный поток |
| `student_store` недоступен/ошибка | KG-апдейты пропускаются (fail-soft), квиз не падает |
| `ENABLE_SCAFFOLDING=false` | ровно текущее поведение (ошибка → объяснение/правильный ответ) |
| `ENABLE_SPACED_REPETITION=false` | карточки не пишутся, review недоступен |
| `USE_AGENT_TUTOR=true` | инструменты `give_hint`/`start_review`/`submit_review`; при недоступности агентной LLM — `deterministic_tutor_step` (уже есть) |

## 9. Тестирование

- **Юнит:** `test_scaffold.py` (hint_for уровни/фолбэк, make_subtasks, лимиты),
  `test_review.py` (SM-2: интервалы, lapse-сброс, due-выборка, dedupe по хэшу),
  расширение `test_student.py` (set_topic/mark_in_progress с явным статусом,
  relations-merge), `test_adaptive.py` — без изменений.
- **Интеграция (стиль `test_graph.py`):**
  - студент ошибается → `quiz.hint` → повтор → верно; ошибается дважды →
    подсказка → подсказка → объяснение; закрытый вопрос (детерминированная оценка).
  - декомпозиция: open-вопрос с `subtasks` → пошаговый разбор → возврат к вопросу.
  - review: seed карточек (tmp `REVIEW_BANK_DIR`) → `POST /review` → `quiz.card`
    с `review=true` → оценка → SM-2 обновлён → `review.done`.
  - KG: ответ → апдейт темы (attempts/correct/mastery) через `deps.student_store`
    (tmp-dir); урок → `in_progress`; mastery-гейт при выборе темы с неосвоенным
    пререквизитом → `system` `kind="mastery.gate"`.
- **API:** `test_api.py` — `GET /students/{id}/review`, `POST /sessions/{id}/review`.
- **Прогон:** `pytest` (текущая база ~550 тестов должна остаться зелёной).

## 10. Демонстрация (для проектной работы)

CLI: сценарий школьника → ошибиться → увидеть подсказку → повторить → после
прохождения квиза выполнить `повтори` и увидеть блиц по должным карточкам.
UI: бейдж/кнопка «Повторить», hint-карточки в чате, статусы в «Моих знаниях».
Логи: `agent.action` для новых инструментов (`give_hint`, `start_review`, `submit_review`).
