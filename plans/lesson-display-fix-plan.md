# План устранения проблемы: урок отображается как сырой JSON

## Резюме проблемы
Урок EduTutor отображается ученику как сырой JSON-блок (с ```json и полями title, hook, definition) вместо структурированных карточек с определением, терминами, секциями и схемой.

## Корневая причина (гипотеза)
Цепочка парсинга прерывается на одном из этапов:
1. LLM возвращает невалидный JSON или JSON который `parse_llm_json` не может разобрать
2. `_lesson_from_data` получает пустой `{}` → `sections` пустой массив
3. `generate_lesson` проверяет `not lesson.sections and not lesson.definition and not lesson.hook` → запускается repair
4. Repair проверяет `text.startsWith("{")` → обнуляет текст, т.к. это JSON
5. На фронтенде `lesson.sections.length === 0` → входим в `<PlainLesson>` который рендерит `text` как простой текст

## Фазы работ

### Фаза 1: Диагностика (добавление логов)

#### 1.1 Backend — логирование в ключевых точках

**Файл:** `src/tutor.py`

Добавить логирование в функцию `generate_lesson` (строка ~493):

```python
import logging
logger = logging.getLogger(__name__)

def generate_lesson(topic, context, state, llm_call=None, on_token=None):
    ...
    raw = generate_text(messages, ..., max_tokens=1200)
    logger.info("generate_lesson[%s]: raw_response_len=%d starts_with=%r", topic, len(raw), raw[:20] if raw else "")
    
    data = parse_llm_json(raw)
    logger.info("generate_lesson[%s]: parsed_data_keys=%r has_sections=%r sections_count=%d", 
                topic, list(data.keys()), "sections" in data, 
                len(data.get("sections", [])) if isinstance(data.get("sections"), list) else 0)
    
    lesson = _lesson_from_data(data, topic)
    logger.info("generate_lesson[%s]: lesson_result_title=%r sections_count=%d hook=%r definition=%r",
                topic, lesson.title, len(lesson.sections), bool(lesson.hook), bool(lesson.definition))
```

**Файл:** `src/agent_tools.py`

Добавить лог после `st.set_lesson(lesson)` (строка ~177):

```python
lesson = tutor_mod.generate_lesson(topic, context, st, llm_call=ctx.llm_call)
logger.info("agent_tools.generate_lesson[%s]: before_set_lesson text_len=%d sections_count=%d",
            topic, len(st.lesson_text or ""), len(st.lesson_sections))
st = st.model_copy(deep=True)
st.set_lesson(lesson)
logger.info("agent_tools.generate_lesson[%s]: after_set_lesson text_len=%d payload_sections=%d",
            topic, len(st.lesson_text or ""), len(st.lesson_sections))
```

#### 1.2 Frontend — логирование в App.jsx

**Файл:** `frontend/src/App.jsx`, строка ~260:

```javascript
case 'tutor.lesson':
    console.log('[EduTutor DEBUG] tutor.lesson event:', JSON.stringify(evt, null, 2))
    console.log('[EduTutor DEBUG] d.lesson:', d.lesson)
    console.log('[EduTutor DEBUG] d.text length:', d.text?.length)
    console.log('[EduTutor DEBUG] d.lesson.sections?:', d.lesson?.sections)
    finalizeStream('lesson', d.text, { topic: d.topic, lesson: d.lesson })
    break
```

#### 1.3 Frontend — логирование в LessonPanel

**Файл:** `frontend/src/components/LessonPanel.jsx`, строка ~57:

```javascript
export default function LessonPanel({ text, topic, lesson }) {
    const raw = lesson && Array.isArray(lesson.sections) && lesson.sections.length > 0 ? lesson : null
    
    console.log('[EduTutor DEBUG] LessonPanel props:', { 
        textLength: text?.length, 
        textStarts: text?.substring(0, 100),
        lessonExists: !!lesson, 
        lessonKeys: lesson ? Object.keys(lesson) : null,
        sectionsCount: lesson?.sections?.length,
        rawResult: !!raw 
    })
    
    // ... rest of code
}
```

---

### Фаза 2: Улучшение robustness парсинга

#### 2.1 Усилить parse_llm_json

**Файл:** `src/tutor.py`, функция `parse_llm_json` (строка 38)

Проблема: текущая реализация использует `text.rfind("}")` для нахождения конца JSON, но если в строковых значениях есть `}`, он будет найден неправильно.

Решение — использовать рекурсивный парсинг скобок:

```python
def parse_llm_json(text: str) -> Dict[str, Any]:
    """Извлечение JSON из ответа LLM (возможен текст вокруг / ```json ```)."""
    text = (text or "").strip()
    if not text:
        return {}
    # Убираем fenced code block
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1).strip()
    # Ищем первую { 
    start = text.find("{")
    if start == -1:
        return {}
    # Используем баланс скобок для точного определения конца
    depth = 0
    end = -1
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == '\\':
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
```

#### 2.2 Улучшить _lesson_from_data

**Файл:** `src/tutor.py`, функция `_lesson_from_data` (строка 427)

Проблема: если LLM вернул `hook` или `definition` но без `sections`, эти поля теряются. Нужно хотя бы создать одну секцию из available контента.

Решение:

```python
def _lesson_from_data(data: Dict[str, Any], topic: str) -> Lesson:
    sections: List[LessonSection] = []
    raw_sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    for s in raw_sections[:4]:
        if not isinstance(s, dict):
            continue
        body = _clean_plain_field(s.get("body"))
        if not body:
            continue
        sections.append(LessonSection(
            heading=_clean_plain_field(s.get("heading")),
            body=body,
            citation=_clean_plain_field(s.get("citation")),
            source=_clean_plain_field(s.get("source")),
            check_question=_clean_plain_field(s.get("check_question")),
        ))
    key_terms = []
    raw_terms = data.get("key_terms") if isinstance(data.get("key_terms"), list) else []
    for t in raw_terms[:5]:
        if isinstance(t, dict):
            term = _clean_plain_field(t.get("term"))
            tdef = _clean_plain_field(t.get("definition"))
            if term and tdef:
                key_terms.append({"term": term, "definition": tdef})
    
    title = _clean_plain_field(data.get("title")) or topic
    hook = _clean_plain_field(data.get("hook"))
    definition = _clean_plain_field(data.get("definition"))
    summary = _clean_plain_field(data.get("summary"))
    
    # Fallback: если нет секций, но есть определение — создать секцию из него
    if not sections and definition:
        sections = [LessonSection(body=definition)]
    
    return Lesson(
        title=title,
        hook=hook,
        definition=definition,
        key_terms=key_terms,
        diagram=_diagram_from_data(data.get("diagram")),
        sections=sections,
        summary=summary,
    )
```

#### 2.3 Усилить repair-логику в generate_lesson

**Файл:** `src/tutor.py`, генерация урока (строка ~512)

Текущая проблема: если ответ начинается с `{`, он полностью отбрасывается:
```python
if text.startswith("{") or text.startswith("[") or text.startswith("\ufeff"):
    text = ""
```

Это означает что даже частично валидный JSON может быть полезным. 

Решение — попробовать несколько стратегий:

```python
# Repair: модель вернула не-JSON / JSON без структуры — собираем из сплошного текста.
if not lesson.sections and not lesson.definition and not lesson.hook:
    text = str(data.get("text") or "").strip()
    if not text and not data:
        # LLM вернул сплошной текст (не JSON) — используем его напрямую
        text = (raw or "").strip()
    
    # Если текст — это JSON-объект — попытаться очистить от маркдаун обёрток
    if text.startswith("{") or text.startswith("["):
        # Попробовать ещё раз через улучшенный парсер
        cleaned = re.sub(r'^```(?:json)?\s*', '', text).rstrip('`').strip()
        try:
            retry_data = json.loads(cleaned)
            if isinstance(retry_data, dict):
                lesson = _lesson_from_data(retry_data, topic)
                if lesson.sections or lesson.definition or lesson.hook:
                    return lesson
        except json.JSONDecodeError:
            pass
        # Очищаем JSON-текст — не показываем сырой JSON в UI
        text = ""
    
    if len(text) < 40:
        text = (context[0] if context else f"Материалы по теме «{topic}» ещё пополняются.")[:1200]
    lesson = _repair_lesson_from_text(text, topic)
```

---

### Фаза 3: Улучшение системного промпта

**Файл:** `src/tutor.py`, `_lesson_prompt` (строка 308)

Проблемные места текущего промпта:
1. Слишком длинный — модель может потеряться
2. Нет явного примера минимальной структуры
3. Diagram-часть добавляется в конец и может перекрывать основную инструкцию

Рекомендации по улучшению:

```python
def _lesson_prompt(topic: str, context: List[str], grade: Optional[str], curriculum: Optional[str]) -> List[Dict[str, str]]:
    system = (
        "Ты — тьютор EduTutor. Составь структурированный УРОК по теме ученика "
        "ТОЛЬКО на основе контекста учебника. "
        + grade_prompt(grade)
        + (f" Учебная программа: {curriculum}." if curriculum else "")
        + (
            "\n\nОБЯЗАТЕЛЬНАЯ СТРУКТУРА ОТВЕТА — строго JSON:\n"
            '{\n'
            '  "title": "Заголовок урока",\n'
            '  "hook": "Интересный вопрос-зацепка?",\n'
            '  "definition": "Краткое определение в 1-2 предложениях.",\n'
            '  "key_terms": [{"term": "термин", "definition": "определение"}],\n'
            '  "sections": [\n'
            '    {"heading": "Подтема 1", "body": "Объяснение 2-4 предложения.", "check_question": "Вопрос?"}\n'
            '  ],\n'
            '  "summary": "Итог в 1-2 предложениях."\n'
            '}\n\n'
            "Правила:\n"
            "- Каждая секция ОБЯЗАТЕЛЬНО должна иметь непустое \"body\"\n"
            "- Минимум 1 секция, максимум 3\n'
            "- Предложения короткие, простые\n'
            '- НЕ выдумывай факты за пределами контекста\n'
            "- Используй именно этот JSON формат"
        )
        + _diagram_grade_hint(grade)
        + (
            "\n\nДИАГРАММА (обязательно):\n"
            "Добавь поле \"diagram\": {\n"
            "  \"kind\": \"flow\",\n"
            "  \"title\": \"Название схемы\",\n"
            "  \"nodes\": [{\"id\": \"n1\", \"label\": \"Термин\"}],\n"
            "  \"edges\": [{\"source\": \"n1\", \"target\": \"n2\", \"label\": \"связь\"}]\n"
            "}."
        )
    )
    ctx = "\n---\n".join(context)[:MAX_EXPLANATION_CHARS]
    user = f"Тема: {topic}\nКонтекст учебника:\n{ctx}"
    return [{"role": "system", content=system}, {"role": "user", "content=user}]
```

---

### Фаза 4: Защита на уровне фронтенда

#### 4.1 Добавить заглушку при неизвестном состоянии урока

**Файл:** `frontend/src/components/LessonPanel.jsx`

```javascript
// Новый компонент-заглушка
function LessonFallback({ text, topic }) {
  return (
    <div className="lesson">
      {topic && <div className="lesson-topic">{'📖'} Урок: {topic}</div>}
      <div className="lesson-fallback">
        <p>Содержимое урока временно недоступно.</p>
        <pre style={{ fontSize: '11px', maxHeight: '200px', overflow: 'auto' }}>
          {String(text || '').substring(0, 500)}
        </pre>
      </div>
    </div>
  )
}
```

#### 4.2 Улучшить условие входа в структурированный режим

**Файл:** `frontend/src/components/LessonPanel.jsx`, строка ~57

```javascript
export default function LessonPanel({ text, topic, lesson }) {
    // Структурированный режим: lesson !== null AND (sections не пустой ИЛИ есть хоть какое-то содержимое)
    const structuredCondition = lesson && typeof lesson === 'object' && (
        (Array.isArray(lesson.sections) && lesson.sections.length > 0) ||
        lesson.definition || 
        lesson.hook || 
        (Array.isArray(lesson.key_terms) && lesson.key_terms.length > 0)
    )
    
    const raw = structuredCondition ? lesson : null
    // ...
}
```

#### 4.3 Усилить clean() — логирование очищенных полей

```javascript
function clean(v, field = '?') {
  const s = String(v ?? '').trim().replace(/^\ufeff/, '')
  if (!s) return ''
  if (s.startsWith('{') || s.startsWith('[')) {
    console.warn(`[LessonPanel] clean() filtered JSON-like value in field="${field}", preview:`, s.substring(0, 60))
    return ''
  }
  // ...
}
```

---

### Фаза 5: Тестирование

#### 5.1 Unit-тесты для parse_llm_json

Добавить тесты в `tests/test_tutor.py`:

```python
@pytest.mark.parametrize("input_str,expected_contains", [
    ('{"title": "test", "sections": []}', ['title']),
    ('```json\n{"title": "test"}\n```', ['title']),
    ('Some text before {"title": "test"} trailing', ['title']),
    ('{"title": "test", "nested": {"a": 1}}', ['title', 'nested']),
    ('{\n  "sections": [\n    {"body": "hello"}\n  ]\n}', ['sections']),
    ('Not valid JSON {{{', []),
    ('{"broken": json}', []),
])
def test_parse_llm_json(input_str, expected_contains):
    result = parse_llm_json(input_str)
    for key in expected_contains:
        assert key in result
```

#### 5.2 End-to-end тест

```javascript
// frontend/src/__tests__/LessonPanel.test.jsx
describe('LessonPanel edge cases', () => {
    it('renders structured lesson when lesson has empty sections but definition', () => {
        const lesson = { 
            title: 'Test', 
            sections: [], 
            definition: 'This is a definition',
            hook: 'What is this?'
        }
        render(<LessonPanel lesson={lesson} text="" topic="Test" />)
        expect(screen.getByText(/This is a definition/)).toBeInTheDocument()
    })

    it('does not render raw JSON as plain text', () => {
        const lesson = { 
            title: '{"title": "raw json"}',  // JSON stringified in title
            sections: []
        }
        render(<LessonPanel lesson={lesson} text='{"title":"test"}' topic="Test" />)
        expect(screen.queryByText(/\{\"title"/)).not.toBeInTheDocument()
    })
})
```

---

## Приоритизация

| Приоритет | Фаза | Обоснование |
|-----------|------|-------------|
| P0 | 1.1-1.3 | Диагностика — без логов не понять где ломается |
| P0 | 2.1 | fix parse_llm_json — базовый надёжный парсинг |
| P1 | 2.2 | fallback _lesson_from_data — защита от потери контента |
| P1 | 4.2 | Улучшение условия структурированного режима |
| P2 | 2.3 | Усиление repair-логики |
| P2 | 3 | Улучшение системного промпта |
| P2 | 4.1-4.3 | Защита фронтенда |
| P3 | 5 | Тесты |

## Критерии готовности

- [ ] Логирование добавлено во все 5 точек
- [ ] `parse_llm_json` проходит все unit-тесты
- [ ] `_lesson_from_data` создаёт секцию из definition при пустом sections
- [ ] `generate_lesson` пытается retry-parsing перед fallback
- [ ] Системный промпт содержит явный пример структуры
- [ ] `LessonPanel` корректно обрабатывает частичные данные
- [ ] Все unit-тесты проходят
- [ ] E2E тест покрывает edge case с сырым JSON
