# Исправление 8 замечаний к EduTutor

Обнаружено 8 замечаний по работе чат-репетитора, UI левой панели и качеству данных. Ниже — результаты анализа и конкретные правки.

## Замечание 1 — Чат повторяет урок вместо ответа на вопрос

**Корневая причина**: В [`content_node`](file:///c:/otus/project_work/src/graph.py#L1082-L1111) на строках 1082-1111 обрабатывается `pending_answer` после урока. Логика:

1. Если ответ = «да»/«готов» → подтверждение квиза ✅  
2. Если `_looks_like_free_question(raw)` → `_answer_free_question` ✅  
3. **else → `st.clear_lesson()` и перегенерация** ❌ ← баг здесь

Проблема в **ветке else (строка 1107-1111)**: если `_looks_like_free_question` не распознала вопрос (например, короткая фраза из 1-2 слов вроде «дроби пример», или ответ без знака вопроса), урок очищается и генерируется заново — пользователь видит повтор.

Функция [`_looks_like_free_question`](file:///c:/otus/project_work/src/agent_loop.py#L385-L401) возвращает `True` только для фраз с `?`, ключевыми словами или 3+ слов. Двухсловные вопросы («это важно», «какие типы») не проходят → урок сбрасывается.

### Исправление

#### [MODIFY] [graph.py](file:///c:/otus/project_work/src/graph.py)

**Строки 1100-1111**: Поменять порядок ветвей — else-ветка (сброс урока) должна срабатывать только для явных «нет»/отказов, а все неопознанные ответы перенаправлять на `_answer_free_question`:

```python
# Было: else → clear_lesson (повтор). 
# Стало: else → _answer_free_question (отвечаем, не повторяем урок)
elif _is_not_ready(raw):  # явный "нет" / "не готов"
    st.clear_lesson()
    ...
else:
    # Неизвестный ответ → считаем свободным вопросом
    st.agent_message = _answer_free_question(st, deps, raw)
    ...
```

---

## Замечание 2 — Заголовки аккордеонов на английском

**Анализ**: Маппинг `SECTION_LABELS` УЖЕ определён в [`LessonPanel.jsx:14-21`](file:///c:/otus/project_work/frontend/src/components/LessonPanel.jsx#L14-L28) с русскими названиями (`content→'Подробное объяснение'`, `summary→'Краткий итог'`, `check→'Проверь себя'`). Функция `sectionLabel()` применяется в `ContentSections` на строке 484. Но маппинг содержит только lowercase-варианты, а бэкенд может отдавать разные регистры.

### Исправление

#### [MODIFY] [LessonPanel.jsx](file:///c:/otus/project_work/frontend/src/components/LessonPanel.jsx)

Проверить, что `sectionLabel` корректно вызывается для всех путей рендеринга. Добавить больше вариантов ключей (title-case, иные русские варианты):

```javascript
const SECTION_LABELS = {
  content: 'Подробное объяснение',
  summary: 'Краткий итог',
  check: 'Проверь себя',
  'check yourself': 'Проверь себя',
  'Проверь себя': 'Проверь себя',
  'Краткий итог': 'Краткий итог',
  'key_terms': 'Ключевые понятия',
  definition: 'Определение',
  hook: 'Введение',
}
```

---

## Замечание 3 — Дубликация «Источники» в левой панели

**Анализ**: [`SourceWhitelistPanel.jsx`](file:///c:/otus/project_work/frontend/src/components/SourceWhitelistPanel.jsx) уже объединяет бывшие SourceWhitelistPanel и SourceSearchPanel (комментарий в строке 1). Баннер «Источники» — один, под ним статус поиска + кнопка «Найти учебник». Найденные веб-источники (`webPages`) рендерятся на строках 123-137 — но только если `type === 'page' && url` заполнен. Если бэкенд не передаёт `sources` массив — раздел пуст.

### Исправление

#### [MODIFY] [SourceWhitelistPanel.jsx](file:///c:/otus/project_work/frontend/src/components/SourceWhitelistPanel.jsx)

Когда `sources` пуст и `status` = ready/done — показывать понятное сообщение вместо пустоты. Также показывать `textbookUrl` если загружен PDF:

На строках 113-151 — добавить fallback-текст когда нет ни webPages, ни localPdf:

```jsx
{webPages.length === 0 && localPdf.length === 0 && status === 'ready' && (
  <div className="source-note muted">Источник проиндексирован, материал готов</div>
)}
```

#### [MODIFY] [App.jsx](file:///c:/otus/project_work/frontend/src/App.jsx)

Передать `textbookUrl` в SourceWhitelistPanel (строка 913-923). Уже передаётся `sources`, но нужно убедиться, что `source.sources` содержит данные.

---

## Замечание 4 — История занятий: когда заполняется?

**Анализ**: [`_maybe_log_session_history`](file:///c:/otus/project_work/api/engine.py#L432-L451) вызывается после каждого `run_step` (строка 616). Запись происходит при изменении «снимка»: `(lesson_done, answered_count, quiz_complete)`. При `lesson_done=True` запись СРАЗУ добавляется. На фронте `sessionHistoryReloadKey` обновляется на событиях `tutor.lesson` (строка 333) и `tutor.summary` (строка 343).

**Изоляция по ученику**: [`SessionHistoryPanel`](file:///c:/otus/project_work/frontend/src/components/SessionHistoryPanel.jsx#L33) запрашивает `GET /api/students/{studentId}/sessions` — данные привязаны к `student_id`. Разные ученики видят только свою историю ✅

> [!NOTE]
> **Проблема**: Если `student_id` не задан в `TutorState` (строка 439 — `if not getattr(st, "student_id", None): return`), история не пишется. Нужно убедиться, что `student_id` устанавливается в intake-фазе.

### Исправление

Минимальное — проверить/залогировать, что `student_id` присваивается. Основная логика уже работает. Если `student_id` не выставлен — это конфигурационная проблема, не код.

---

## Замечание 5 — Мусорные темы в графе знаний

**Корневая причина**: Фильтр [`is_junk_topic`](file:///c:/otus/project_work/src/knowledge_graph.py#L150-L157) применяется только в [`_web_headings`](file:///c:/otus/project_work/src/knowledge_graph.py#L230), но **НЕ в методе [`add_topic`](file:///c:/otus/project_work/src/knowledge_graph.py#L252-L278)**. Другие пути добавления узлов (из TOC учебника, из LLM-generated headings) обходят фильтр.

Жёстко захардкоженные "фонетика и орфоэпия", "лексика и фразеология" в `_GRAPH_JUNK_EXACT` (строка 141) — это подозрительно. Эти темы МОГУТ быть легитимными разделами русского языка. Однако пользователь прямо говорит, что это мусор в его контексте — значит, они пришли из навигации страницы, а не из учебника.

### Исправление

#### [MODIFY] [knowledge_graph.py](file:///c:/otus/project_work/src/knowledge_graph.py)

1. В `add_topic()` (строка 261-278) — добавить проверку `is_junk_topic` прямо при добавлении узла:

```python
def add_topic(self, node_id, title, node_type="topic", ...):
    if not allow_url and _is_url_like(title):
        return
    # Новое: фильтрация мусора при добавлении любого узла
    if node_type not in ("book", "lesson") and is_junk_topic(title):
        return
    ...
```

2. Убрать "фонетика и орфоэпия" и "лексика и фразеология" из `_GRAPH_JUNK_EXACT` — они могут быть реальными разделами русского языка. Вместо этого фильтровать по признакам навигации (слишком общие/короткие + не по теме текущего предмета).

> [!IMPORTANT]
> **Вопрос**: «Фонетика и орфоэпия», «Лексика и фразеология» — действительно мусор во всех случаях, или только для математики? Если ученик изучает русский язык, это легитимные темы. Нужно ли добавить привязку фильтра к текущему предмету?

---

## Замечание 6 — OKF-карточка: изложение не кэшируется

**Анализ**: [`enrich_body()`](file:///c:/otus/project_work/src/wiki.py#L495-L536) СОХРАНЯЕТ body через `self.upsert(art)` (строка 533). Frontend [`enrichTopic`](file:///c:/otus/project_work/frontend/src/components/KnowledgeWikiPanel.jsx#L134-L158) после вызова обновляет `reading` (строка 146) и `fetchWiki()` (строка 150).

**Проблема**: При ПОВТОРНОМ открытии модала `reading` устанавливается из `subjects` (строка 245: `setReading({ subject: ..., article: subjects[si].articles[ai] })`). Если `fetchWiki()` ещё не завершился к моменту открытия, article.body будет пустым. Но даже если завершился — нужно проверить, что `to_summary_dict()` включает `body`.

### Исправление

#### [MODIFY] [wiki.py](file:///c:/otus/project_work/src/wiki.py)

Проверить метод `to_summary_dict()` — включает ли он поле `body`. Если нет — добавить.

#### [MODIFY] [KnowledgeWikiPanel.jsx](file:///c:/otus/project_work/frontend/src/components/KnowledgeWikiPanel.jsx)

На строке 146 уже обновляется `reading`. Добавить обновление в `data` state напрямую, чтобы при закрытии/открытии модала body не терялся.

---

## Замечание 7 — Левая панель: дедубликация и оптимизация

**Текущий состав** ([`App.jsx:909-931`](file:///c:/otus/project_work/frontend/src/App.jsx#L909-L931)):

1. `KnowledgeWikiPanel` — База знаний (темы + карточки OKF)
2. `SessionHistoryPanel` — История занятий
3. `SourceWhitelistPanel` — Источники (статус + политика + кнопка «Найти»)
4. `KnowledgeGraphPanel` — Граф знаний (Canvas-созвездие)
5. `FileUpload` — Загрузка файла

**Дубликация**: Минимальная. `SourceWhitelistPanel` уже объединяет бывшие отдельные панели. Но `KnowledgeWikiPanel` и `KnowledgeGraphPanel` показывают ОДНИ И ТЕ ЖЕ темы в двух форматах (список карточек vs canvas-граф). Возможно стоит объединить или сделать переключатель «Список / Граф».

### Исправление

> [!NOTE]
> Полная реорганизация левой панели — крупная задача. В рамках текущих правок предлагаю **не менять структуру**, а только убрать дубликацию контента и улучшить читаемость: свернуть граф по умолчанию если тем мало (<5), убрать FileUpload если файл уже загружен.

---

## Замечание 8 — Общая проверка и мелкие фиксы

- Проверить что билд фронтенда обновлён (npm build)
- Проверить `to_summary_dict()` включает `body`
- Убедиться `student_id` устанавливается в intake

---

## Proposed Changes

### Backend (src/)

#### [MODIFY] [graph.py](file:///c:/otus/project_work/src/graph.py)
Строки 1100-1111: Исправить порядок ветвей — else для «нет», а неопознанные → `_answer_free_question`

#### [MODIFY] [knowledge_graph.py](file:///c:/otus/project_work/src/knowledge_graph.py)
Строки 261-278: Добавить `is_junk_topic` в `add_topic()`

#### [MODIFY] [wiki.py](file:///c:/otus/project_work/src/wiki.py)
Проверить `to_summary_dict()` включает `body`

### Frontend (frontend/src/)

#### [MODIFY] [LessonPanel.jsx](file:///c:/otus/project_work/frontend/src/components/LessonPanel.jsx)
Строки 14-21: Расширить `SECTION_LABELS`

#### [MODIFY] [SourceWhitelistPanel.jsx](file:///c:/otus/project_work/frontend/src/components/SourceWhitelistPanel.jsx)
Строки 113-151: Добавить fallback-сообщение когда sources пуст

#### [MODIFY] [KnowledgeWikiPanel.jsx](file:///c:/otus/project_work/frontend/src/components/KnowledgeWikiPanel.jsx)
Строки 134-158: Обновить data state после enrichment для корректного кэша

---

## Verification Plan

### Automated Tests
```bash
cd c:\otus\project_work
python -m pytest tests/ -x -v
```

### Manual Verification
1. После урока ввести короткий вопрос (2 слова) — убедиться, что урок НЕ повторяется
2. Проверить аккордеон-заголовки — русские названия
3. Сгенерировать изложение в OKF → закрыть → открыть → body показывается
4. Граф не содержит «Фильтры», «Картинки» и т.п.
