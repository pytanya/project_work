"""Тесты LessonEval — детерминированный судья-lite (0 LLM-вызовов, без задержки)."""

from __future__ import annotations

from api.schemas import DiagramEdge, DiagramNode, Lesson, LessonDiagram, LessonSection
from src.lesson_eval import PASS_THRESHOLD, eval_lesson


def _good_lesson() -> Lesson:
    return Lesson(
        title="Атмосфера",
        hook="Почему небо голубое?",
        definition="Атмосфера — газовая оболочка Земли.",
        key_terms=[{"term": "азот", "definition": "главный газ атмосферы"}],
        diagram=LessonDiagram(
            kind="flow",
            nodes=[DiagramNode(id="n1", label="Азот"), DiagramNode(id="n2", label="Кислород")],
            edges=[DiagramEdge(source="n1", target="n2", label="смесь")],
        ),
        sections=[
            LessonSection(heading="Состав", body="Азот и кислород — её основа.", citation="§12"),
            LessonSection(heading="Роль", body="Атмосфера защищает планету.", citation="§12"),
        ],
        summary="Атмосфера защищает жизнь на Земле.",
    )


class TestEvalLesson:
    def test_good_lesson_passes_for_all_grades(self):
        for grade in ("6", "9", "11", None):
            res = eval_lesson(_good_lesson(), grade)
            assert res.avg_score >= PASS_THRESHOLD
            assert res.verdict == "pass"

    def test_student_uses_student_budget(self):
        # без класса — студенческий бюджет
        res = eval_lesson(_good_lesson(), None)
        assert res.grade_budget == "student"

    def test_schoolchild_uses_school_budget(self):
        res = eval_lesson(_good_lesson(), "5")
        assert res.grade_budget == "school"

    def test_missing_structure_penalized(self):
        lesson = Lesson(sections=[LessonSection(body="только текст")])
        res = eval_lesson(lesson, "7")
        assert res.criteria["structure"] < 0.4
        assert res.criteria["citations"] == 0.0

    def test_missing_citations_penalized(self):
        lesson = Lesson(
            hook="h",
            definition="d",
            sections=[
                LessonSection(body="Азот и кислород — основа атмосферы."),
                LessonSection(body="Атмосфера защищает планету."),
            ],
            summary="s",
            key_terms=[{"term": "азот", "definition": "газ"}],
        )
        res = eval_lesson(lesson, "7")
        assert res.criteria["citations"] == 0.0

    def test_diagram_contradicting_text_penalized(self):
        # схема вводит понятие «Квазар», которого нет в тексте/терминах
        lesson = Lesson(
            hook="h",
            definition="Атмосфера — оболочка.",
            key_terms=[{"term": "азот", "definition": "газ"}],
            diagram=LessonDiagram(
                kind="map",
                nodes=[DiagramNode(id="n1", label="Квазар", x=0.5, y=0.5)],
            ),
            sections=[LessonSection(body="Атмосфера — оболочка из газов.")],
            summary="s",
        )
        res = eval_lesson(lesson, "7")
        assert res.criteria["diagram"] < 0.5

    def test_diagram_consistent_with_text(self):
        lesson = Lesson(
            hook="h",
            definition="Атмосфера — оболочка.",
            key_terms=[{"term": "азот", "definition": "газ"}],
            diagram=LessonDiagram(
                kind="flow",
                nodes=[DiagramNode(id="n1", label="Азот"), DiagramNode(id="n2", label="Кислород")],
            ),
            sections=[LessonSection(body="Азот и кислород — основа атмосферы.")],
            summary="s",
        )
        res = eval_lesson(lesson, "7")
        assert res.criteria["diagram"] >= 0.6

    def test_no_diagram_is_neutral_not_fail(self):
        # урок без схемы не должен проваливаться из-за отсутствия dual-coding
        lesson = Lesson(
            hook="h",
            definition="Атмосфера — оболочка.",
            sections=[LessonSection(body="Азот и кислород — основа атмосферы.", citation="§12")],
            summary="s",
        )
        res = eval_lesson(lesson, "7")
        assert res.criteria["diagram"] == 0.5
        assert res.verdict == "pass"

    def test_long_sentences_penalized_for_schoolchild(self):
        long_body = "Атмосфера это газовая оболочка которая окружает планету со всех сторон и " \
                    "состоит из большого количества разных газов но главными являются азот и " \
                    "кислород причём азота почти восемьдесят процентов а кислорода чуть больше " \
                    "двадцати и это очень важно для дыхания всех живых существ на Земле потому " \
                    "что без кислорода жизнь была бы невозможна совершенно поэтому атмосфера " \
                    "играет огромную роль в жизни планеты."
        lesson = Lesson(
            hook="h",
            definition="Атмосфера — оболочка.",
            sections=[LessonSection(body=long_body, citation="§12")],
            summary="s",
        )
        res_school = eval_lesson(lesson, "6")
        assert res_school.criteria["readability"] < 0.5
        # тот же текст для студента — мягче (его бюджет больше)
        res_student = eval_lesson(lesson, None)
        assert res_student.criteria["readability"] >= res_school.criteria["readability"]

    def test_to_dict_serializable(self):
        d = eval_lesson(_good_lesson(), "7").to_dict()
        assert set(d) == {"criteria", "avg_score", "verdict", "grade_budget"}
        assert 0.0 <= d["avg_score"] <= 1.0


class TestSourceMetadata:
    """Метаданные источника заполняют citation секций → groundedness честный."""

    def test_apply_source_metadata_section_number(self):
        from src.tutor import _apply_source_metadata
        lesson = Lesson(
            title="Атмосфера",
            sections=[
                LessonSection(heading="Состав", body="Азот и кислород — основа."),
                LessonSection(heading="Роль", body="Атмосфера защищает планету."),
            ],
        )
        _apply_source_metadata(lesson, [
            {"source": "Учебник География", "section_number": "12"},
            {"source": "Учебник География", "section_number": "12"},
        ])
        assert lesson.sections[0].citation == "§12"
        assert lesson.sections[1].citation == "§12"
        assert lesson.sections[0].source == "Учебник География"

    def test_apply_source_metadata_url_becomes_domain(self):
        from src.tutor import _apply_source_metadata
        lesson = Lesson(sections=[LessonSection(body="текст")])
        _apply_source_metadata(lesson, [{"source": "https://www.infourok.ru/konspekt"}])
        assert lesson.sections[0].citation == "infourok.ru"

    def test_apply_source_metadata_inline_fallback(self):
        # нет метаданных → ищем §N/страницу прямо в теле секции
        from src.tutor import _apply_source_metadata, _extract_inline_citation
        # напрямую хелпер
        assert _extract_inline_citation("Подробнее в §5 учебника.") == "§5"
        lesson = Lesson(sections=[LessonSection(body="Определение по §5 источника.")])
        _apply_source_metadata(lesson, None)
        assert lesson.sections[0].citation == "§5"

    def test_source_metadata_makes_citations_nonzero(self):
        # после применения метаданных citations перестаёт быть 0 → судья не режет groundedness
        from src.tutor import _apply_source_metadata
        lesson = Lesson(
            title="Атмосфера",
            hook="Почему небо голубое?",
            definition="Атмосфера — газовая оболочка.",
            sections=[
                LessonSection(heading="Состав", body="Азот и кислород — основа."),
                LessonSection(heading="Роль", body="Атмосфера защищает планету."),
            ],
            summary="Атмосфера защищает жизнь на Земле.",
        )
        _apply_source_metadata(lesson, [
            {"source": "Учебник", "section_number": "2"},
            {"source": "Учебник", "section_number": "3"},
        ])
        res = eval_lesson(lesson, "7")
        assert res.criteria["citations"] >= 0.5

    def test_existing_citation_not_overwritten(self):
        from src.tutor import _apply_source_metadata
        lesson = Lesson(sections=[LessonSection(body="текст", citation="стр. 9")])
        _apply_source_metadata(lesson, [{"source": "Другое", "section_number": "12"}])
        # уже проставленная цитата не затирается чужими метаданными
        assert lesson.sections[0].citation == "стр. 9"
        assert lesson.sections[0].source == ""

