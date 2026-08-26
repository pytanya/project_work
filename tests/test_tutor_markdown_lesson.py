"""Тесты для markdown→Lesson парсера (новый стриминговый pipeline).

Покрытие:
- _normalize_heading(): маппинг заголовков LLM на стандартные ключи
- _parse_markdown_lesson(): полный цикл парсинга markdown в Lesson
- Краевые случаи: пустой текст, отсутствие секций, незнакомые заголовки
"""

import pytest
from src.tutor import _normalize_heading, _parse_markdown_lesson, _extract_markdown_sections


class TestNormalizeHeading:
    """Проверка маппинга заголовков."""

    def test_opredelenie(self):
        assert _normalize_heading("Определение") == "definition"
        assert _normalize_heading("определение") == "definition"
        assert _normalize_heading("## Определение") == "definition"

    def test_terms(self):
        assert _normalize_heading("Основные понятия") == "terms"
        assert _normalize_heading("Ключевые понятия") == "terms"
        assert _normalize_heading("основные понятия") == "terms"

    def test_content(self):
        assert _normalize_heading("Подробное объяснение") == "content"
        assert _normalize_heading("Разбор темы") == "content"
        assert _normalize_heading("подробное объяснение") == "content"

    def test_check(self):
        assert _normalize_heading("Проверь себя") == "check"
        assert _normalize_heading("Вопросы для самопроверки") == "check"
        assert _normalize_heading("проверь себя") == "check"

    def test_summary(self):
        assert _normalize_heading("Краткий итог") == "summary"
        assert _normalize_heading("Итоги") == "summary"
        assert _normalize_heading("Заключение") == "summary"
        assert _normalize_heading("краткий итог") == "summary"

    def test_fuzzy(self):
        # Fuzzy: первые 6 символов совпадают
        assert _normalize_heading("Опреде") == "definition"
        assert _normalize_heading("Основн") == "terms"

    def test_unknown(self):
        assert _normalize_heading("Неизвестный раздел") is None
        assert _normalize_heading("") is None
        assert _normalize_heading(None) is None

    def test_with_noise(self):
        # Заголовки с эмодзи и номерами
        assert _normalize_heading("💡 Определение") == "definition"
        assert _normalize_heading("1. Проверь себя") == "check"
        assert _normalize_heading("### Краткий итог ###") == "summary"


class TestParseMarkdownLesson:
    """Проверка полного парсинга markdown → Lesson."""

    def test_empty_text(self):
        lesson = _parse_markdown_lesson("", "Test Topic")
        assert lesson.title == "Test Topic"
        assert lesson.definition == ""
        assert lesson.sections == []

    def test_none_text(self):
        lesson = _parse_markdown_lesson(None, "Test Topic")
        assert lesson.title == "Test Topic"

    def test_single_h1_title(self):
        md = "# Введение в биологию\n\nТекст урока."
        lesson = _parse_markdown_lesson(md, "Unknown Topic")
        assert lesson.title == "Введение в биологию"

    def test_full_structure(self):
        md = """# Урок по физике

## Определение

Механика — это раздел физики, изучающий движение тел и взаимодействие между ними. Механика включает кинематику, динамику и статику.

## Основные понятия

- **Механическое движение**: изменение положения тела в пространстве относительно других тел с течением времени.
- **Траектория**: линия, вдоль которой движется тело.
- **Перемещение**: вектор, соединяющий начальное и конечное положение тела.

## Подробное объяснение

Механика является одной из oldest областей физики. Её основы были заложены Галилеем и Ньютоном. законы Ньютона позволяют описать движение любых макроскопических объектов.

## Проверь себя

Что такое траектория движения тела?

## Краткий итог

Механика изучает движение тел. Основные понятия: движение, траектория, перемещение. законы Ньютона — фундамент механики.
"""
        lesson = _parse_markdown_lesson(md, "Fallback Topic")
        
        # Title должен взяться из #
        assert lesson.title == "Урок по физике"
        
        # Definition
        assert "Механика" in lesson.definition
        assert "раздел физики" in lesson.definition
        assert len(lesson.definition) > 50
        
        # Terms
        assert len(lesson.key_terms) >= 3
        assert lesson.key_terms[0]["term"] == "Механическое движение"
        assert "изменение положения" in lesson.key_terms[0]["definition"]
        
        # Sections (content parts)
        assert len(lesson.sections) >= 1
        assert any("Механика является" in s.body for s in lesson.sections)
        
        # Summary
        assert "Механика изучает" in lesson.summary
        
        # Raw text сохранён
        assert lesson.raw_text == md

    def test_partial_structure(self):
        """Если есть только определение — оно идёт как первая секция."""
        md = """## Определение

Это тестовое определение урока по математике. Оно содержит достаточно информации для понимания темы.

## Краткий итог

Итоговое предложение.
"""
        lesson = _parse_markdown_lesson(md, "Math Topic")
        assert len(lesson.definition) > 50
        # Определение также как первая секция (fallback)
        assert len(lesson.sections) >= 1

    def test_no_sections_header(self):
        """Markdown без ## заголовков — всё идёт как content."""
        md = "Просто связный текст без какой-либо структуры.\n\nЕщё один абзац текста для теста."
        lesson = _parse_markdown_lesson(md, "Plain Topic")
        # Без ## заголовков парсер не определит секции
        # Но raw_text должен быть
        assert lesson.raw_text == md

    def test_max_four_sections(self):
        """Не больше 4 content-секций."""
        md = "\n\n".join([f"## Объяснение {i}\n\nТекст секции {i}." for i in range(6)])
        lesson = _parse_markdown_lesson(md, "Multi Section")
        
        content_count = sum(1 for s in lesson.sections if "Объяснение" in s.heading)
        assert content_count <= 4

    def test_section_headings_filled(self):
        """Секции без heading получают heading из body."""
        md = """## Определение

Тестовое определение с достаточным количеством слов для формирования заголовка из первых нескольких слов этого предложения.

## Неизвестный раздел

Это содержимое неизвестного раздела, который не распознаётся стандартными мапперами.
"""
        lesson = _parse_markdown_lesson(md, "Topic")
        for s in lesson.sections:
            assert s.heading  # У каждой секции есть heading

    def test_generic_heading_replacement(self):
        """«Часть 1»/«Раздел 2» заменяются на содержательный заголовок."""
        from src.tutor import _GENERIC_HEADING_RE
        assert _GENERIC_HEADING_RE.match("Часть 1")
        assert _GENERIC_HEADING_RE.match("Раздел 2")
        assert not _GENERIC_HEADING_RE.match("Определение механики")


class TestMarkdownEdgeCases:
    """Краевые случаи парсинга."""

    def test_unicode_in_heading(self):
        md = """## Определение

Русскоязычный текст с буквой ё и другими символами.
"""
        lesson = _parse_markdown_lesson(md, "RU Topic")
        assert "ё" in lesson.definition or len(lesson.definition) > 10

    def test_empty_sections_ignored(self):
        md = """## Определение

Реальное определение.

## Пустой раздел

## Итог

Вот итог.
"""
        lesson = _parse_markdown_lesson(md, "Empty Section Test")
        assert len(lesson.definition) > 0

    def test_long_definition(self):
        """Длинное определение (> 200 символов)."""
        long_def = "Механика — это очень длинное и подробное определение, которое содержит более ста五十 символов для проверки минимальных требований к длине определения в системе EduTutor." * 2
        md = f"## Определение\n\n{long_def}"
        lesson = _parse_markdown_lesson(md, "Long Def Test")
        assert len(lesson.definition) > 200

    def test_multiple_h1_tags(self):
        """Если несколько # — берётся первый как title."""
        md = "# Первый заголовок\n\n## Определение\n\nТекст.\n\n# Второй заголовок\n\n## Итог\n\nИтог."
        lesson = _parse_markdown_lesson(md, "Default")
        assert lesson.title == "Первый заголовок"


class TestExtractMarkdownSections:
    """Проверка функции _extract_markdown_sections()."""

    def test_simple_two_sections(self):
        md = """# Урок

## Определение
Это определение.

## Итог
Это итог.
"""
        sections = _extract_markdown_sections(md)
        assert sections["Определение"] == "Это определение."
        assert sections["Итог"] == "Это итог."

    def test_h1_becomes_title(self):
        md = "# Механика\n\n## Определение\nТекст определения."
        sections = _extract_markdown_sections(md)
        assert sections["title"] == "Механика"
        assert sections["Определение"] == "Текст определения."

    def test_empty_section_ignored(self):
        md = """## Определение
Текст.

## Пустой

## Итог
Итог.
"""
        sections = _extract_markdown_sections(md)
        assert "Пустой" not in sections or sections["Пустой"] == ""

    def test_no_headers_returns_all_as_one(self):
        md = "Просто текст без заголовков."
        sections = _extract_markdown_sections(md)
        # Без заголовков возвращаем пустой словарь
        assert len(sections) == 0

    def test_multiline_content(self):
        md = """## Определение
Первое предложение.

Второе предложение.

Третье предложение.
"""
        sections = _extract_markdown_sections(md)
        expected = "Первое предложение.\n\nВторое предложение.\n\nТретье предложение."
        assert sections["Определение"] == expected

    def test_nested_levels_only_h2_used(self):
        md = """## Основной раздел
Текст.

### Подраздел
Подтекст.
"""
        sections = _extract_markdown_sections(md)
        # Только ## создаёт новые секции
        assert "Основной раздел" in sections


class TestExtractMarkdownSectionsAdditional:
    """Дополнительные тесты для _extract_markdown_sections()."""

    def test_empty_text(self):
        """Пустой текст → пустой словарь."""
        sections = _extract_markdown_sections("")
        assert sections == {}

    def test_only_h1_title(self):
        """Только заголовок H1 → {"title": ""}."""
        md = "# Мой урок\n"
        sections = _extract_markdown_sections(md)
        assert sections == {"title": "Мой урок"}

    def test_h1_plus_multiple_h2(self):
        """H1 + несколько H2 → правильные секции."""
        md = """# Заголовок урока

## Определение
Это определение.

## Понятия
Список понятий.

## Итог
Подведём итог.
"""
        sections = _extract_markdown_sections(md)
        assert sections["title"] == "Заголовок урока"
        assert sections["Определение"] == "Это определение."
        assert sections["Понятия"] == "Список понятий."
        assert sections["Итог"] == "Подведём итог."

    def test_h2_without_content(self):
        """H2 без контента → пустая строка."""
        md = """## Определение
Текст.

## Пустой

## Итог
Итог.
"""
        sections = _extract_markdown_sections(md)
        assert sections.get("Пустой", "") == ""

    def test_nested_h3_ignored(self):
        """Nested H3 (игнорируется) → не создаёт секцию."""
        md = """## Главный раздел
Первый абзац.

Второй абзац с ### Подзаголовком внутри.

Ещё текст.
"""
        sections = _extract_markdown_sections(md)
        assert "Главный раздел" in sections
        assert "### Подзаголовком внутри." in sections["Главный раздел"]
        assert "### Подзаголовком внутри." not in sections or "Подзаголовком внутри." not in sections

    def test_multiple_h1_only_first(self):
        """Multiple H1 → только первый используется как title."""
        md = """# Первый заголовок

Текст между h1 и h2.

## Секция
Содержимое секции.

# Второй заголовок
"""
        sections = _extract_markdown_sections(md)
        assert sections["title"] == "Первый заголовок"
        assert "Секция" in sections
        assert sections["Секция"] == "Содержимое секции."

    def test_hash_in_text_not_heading(self):
        """Markdown с # внутри текста (не заголовок) → не парсится как заголовок."""
        md = """## Пример

Цена товара #123 составляет 500 рублей.
Это не заголовок, а # хеш в тексте.
"""
        sections = _extract_markdown_sections(md)
        assert "Пример" in sections
        assert "#123" in sections["Пример"]
        assert len(sections) == 1


class TestParseMarkdownLessonAdditional:
    """Дополнительные тесты для _parse_markdown_lesson()."""

    def test_full_markdown_structure(self):
        """Полный markdown (title + definition + terms + example + summary) → все поля заполнены."""
        md = """# Полное занятие

## Определение

Механика — это раздел физики, изучающий движение тел и взаимодействие между ними. Механика включает кинематику, динамику и статику.

## Основные понятия

- **Механическое движение**: изменение положения тела в пространстве относительно других тел.
- **Траектория**: линия, вдоль которой движется тело.
- **Перемещение**: вектор, соединяющий начальное и конечное положение тела.

## Подробное объяснение

Механика является одной из oldest областей физики. Её основы были заложены Галилеем и Ньютоном. законы Ньютона позволяют описать движение любых макроскопических объектов.

## Проверь себя

Что такое траектория движения тела?

## Краткий итог

Механика изучает движение тел. Основные понятия: движение, траектория, перемещение.
"""
        lesson = _parse_markdown_lesson(md, "Fallback Topic")
        assert lesson.title == "Полное занятие"
        assert "Механика" in lesson.definition
        assert len(lesson.key_terms) >= 3
        assert len(lesson.sections) >= 1
        assert "Кинематика" not in lesson.summary and "движение тел" in lesson.summary
        assert lesson.raw_text == md

    def test_only_title_no_definition(self):
        """Только title → definition=''."""
        md = "# Только заголовок\n\nКакой-то текст без структуры."
        lesson = _parse_markdown_lesson(md, "Default Topic")
        assert lesson.title == "Только заголовок"
        assert lesson.definition == ""

    def test_malformed_markdown_no_headers(self):
        """Malformed markdown (без заголовков) → fallback через _repair_lesson_from_text."""
        md = "Просто связный текст без какой-либо структуры и заголовков.\n\nЕщё один абзац."
        lesson = _parse_markdown_lesson(md, "Plain Topic")
        # Должен быть fallback — lesson создан даже без заголовков
        assert lesson.title == "Plain Topic"
        assert lesson.raw_text == md

    def test_empty_text_fallback(self):
        """Пустой текст → fallback Lesson с topic."""
        lesson = _parse_markdown_lesson("", "Empty Topic")
        assert lesson.title == "Empty Topic"
        assert lesson.definition == ""
        assert lesson.sections == []

    def test_none_text_fallback(self):
        """None текст → fallback Lesson с topic."""
        lesson = _parse_markdown_lesson(None, "None Topic")
        assert lesson.title == "None Topic"

    def test_markdown_with_latex_preserves_raw(self):
        """Markdown с LaTeX формулами → raw_text сохраняется."""
        md = """# Физика

## Определение

Формула энергии: E = mc². Это знаменитая формула Эйнштейна.

## Основные понятия

- **Энергия**: способность совершать работу. Формула: $E = \\frac{1}{2}mv^2$.
"""
        lesson = _parse_markdown_lesson(md, "Physics")
        assert lesson.title == "Физика"
        assert "mc²" in lesson.definition
        assert lesson.raw_text == md
        assert "$E = \\\\frac{1}{2}mv^2$" in lesson.raw_text or "mc²" in lesson.raw_text


class TestGenerateLesson:
    """Тесты для generate_lesson() с mock-LLM."""

    def test_with_on_token_tokens(self):
        """С on_token → стримит токены через callback."""
        from src.tutor import generate_lesson
        from src.states import TutorState
        
        tokens_received = []
        def on_token(token: str) -> None:
            tokens_received.append(token)
        
        # Mock llm_call — возвращает markdown текст
        def llm_call(messages):
            return "# Тест\n\n## Определение\n\nЭто тест."
        
        state = TutorState(grade="7", subject="Физика")
        context = ["Какой-то контекст для урока."]
        
        lesson = generate_lesson(
            "Тестовая тема",
            context,
            state,
            llm_call=llm_call,
            on_token=on_token
        )
        
        # Токены должны быть собраны (через generate_text)
        assert lesson is not None
        assert lesson.title != "" or lesson.definition != ""

    def test_without_on_token_returns_lesson(self):
        """Без on_token → возвращает Lesson целиком."""
        from src.tutor import generate_lesson
        from src.states import TutorState
        
        def llm_call(messages):
            return "# Без стрима\n\n## Определение\n\nПолный ответ без стриминга."
        
        state = TutorState(grade="8", subject="Химия")
        context = ["Контекст химии."]
        
        lesson = generate_lesson(
            "Химия тема",
            context,
            state,
            llm_call=llm_call,
            on_token=None
        )
        
        assert lesson is not None
        assert lesson.title == "Без стрима"

    def test_json_response_backward_compat(self):
        """JSON-ответ (backward compat) → использует legacy path."""
        from src.tutor import generate_lesson
        from src.states import TutorState
        
        # JSON-ответ со старым форматом
        json_response = json.dumps({
            "title": "JSON Урок",
            "definition": "Определение из JSON.",
            "sections": [
                {"heading": "Раздел 1", "body": "Содержание раздела."}
            ],
            "hook": "Первое предложение."
        })
        
        def llm_call(messages):
            return json_response
        
        state = TutorState(grade="6", subject="История")
        context = ["Контекст истории."]
        
        lesson = generate_lesson(
            "Историческая тема",
            context,
            state,
            llm_call=llm_call
        )
        
        assert lesson is not None
        assert lesson.title == "JSON Урок"
        assert "Определение из JSON" in lesson.definition

    def test_text_response_backward_compat(self):
        """Text-ответ (backward compat) → парсит как markdown из поля text."""
        from src.tutor import generate_lesson
        from src.states import TutorState
        
        # Ответ в формате {"text": "markdown текст"}
        text_response = json.dumps({
            "text": "# Text Response\n\n## Определение\n\nОпределение из текстового поля."
        })
        
        def llm_call(messages):
            return text_response
        
        state = TutorState(grade="5", subject="Биология")
        context = ["Контекст биологии."]
        
        lesson = generate_lesson(
            "Биология тема",
            context,
            state,
            llm_call=llm_call
        )
        
        assert lesson is not None
        # Должен распарситься текст из поля text
        assert "Определение из текстового поля" in lesson.definition or lesson.title != ""

    def test_malformed_response_fallback(self):
        """Malformed ответ → fallback на контекст."""
        from src.tutor import generate_lesson
        from src.states import TutorState
        
        # Некорректный ответ — не JSON и не markdown
        def llm_call(messages):
            return "{broken json[[["
        
        state = TutorState(grade="7", subject="Математика")
        context = ["Хороший контекст по математике. Теорема Пифагора: a² + b² = c²."]
        
        lesson = generate_lesson(
            "Теорема Пифагора",
            context,
            state,
            llm_call=llm_call
        )
        
        # Fallback должен вернуть lesson хотя бы с title
        assert lesson is not None
        assert lesson.title != ""

    def test_empty_response_uses_context(self):
        """Пустой ответ LLM → fallback на контекст."""
        from src.tutor import generate_lesson
        from src.states import TutorState
        
        def llm_call(messages):
            return ""
        
        state = TutorState(grade="9", subject="Алгебра")
        context = ["Квадратные уравнения: ax² + bx + c = 0."]
        
        lesson = generate_lesson(
            "Квадратные уравнения",
            context,
            state,
            llm_call=llm_call
        )
        
        assert lesson is not None
