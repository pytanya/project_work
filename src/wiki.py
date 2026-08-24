"""
EduTutor — Knowledge Wiki (roadmap #2): персистентная база знаний ученика.

Каждая тема, изученная в любой сессии, накапливается в wiki-статье
(markdown + YAML-frontmatter, формат OKF v0.2) в каталоге
`data/knowledge_wiki/<subject>/<topic>.md`. Мастерство, число попыток,
слабые места и пояснения переживают перезапуски и между сессиями.

Отличие от `okf.emit_okf_bundle` (экспорт знаний учебника на диск):
Wiki — это *накопление* состояния ученика по темам поверх источников,
обновляется при каждой оценке ответа и завершении квиза.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .config import settings as default_settings

OKF_VERSION = "0.2"
GENERATOR = "edututor/wiki-0.1"

# Файл-индекс предмета (список тем с мастерством) — по желанию, не обязателен
_INDEX_NAME = "_index.md"


def _slug(text: str) -> str:
    """Имя файла/каталога из названия (латиница/цифры/дефис; юникод-безопасно)."""
    out = []
    for ch in (text or "").lower():
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        elif ch in " /\\":
            out.append("-")
    s = "".join(out).strip("-")
    return s or "topic"


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class WikiNote:
    """Структурированная запись об ошибке/результате по теме.

    Поля question, student_answer, correct_answer — опциональны.
    Если контекст диалога недоступен, сохраняется только feedback.
    """

    __slots__ = ("date", "question", "student_answer", "feedback", "correct_answer")

    def __init__(
        self,
        date: str,
        feedback: str,
        question: Optional[str] = None,
        student_answer: Optional[str] = None,
        correct_answer: Optional[str] = None,
    ) -> None:
        self.date = date
        self.question = question
        self.student_answer = student_answer
        self.feedback = feedback
        self.correct_answer = correct_answer

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"date": self.date, "feedback": self.feedback}
        if self.question:
            d["question"] = self.question
        if self.student_answer:
            d["student_answer"] = self.student_answer
        if self.correct_answer:
            d["correct_answer"] = self.correct_answer
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WikiNote":
        """Создать WikiNote из dict. Поддерживает как новый формат, так и парсинг старой строки."""
        # Новый формат — есть поля даты
        date = data.get("date", "")
        feedback = data.get("feedback", "")
        question = data.get("question")
        student_answer = data.get("student_answer")
        correct_answer = data.get("correct_answer")
        return cls(
            date=date,
            feedback=feedback,
            question=question,
            student_answer=student_answer,
            correct_answer=correct_answer,
        )

    @classmethod
    def from_legacy_string(cls, note_str: str) -> "WikiNote":
        """Парсинг старой строковой заметки формата "{дата}: {feedback}"."""
        parts = note_str.split(": ", 1)
        date = parts[0].strip() if parts else ""
        feedback = parts[1].strip() if len(parts) > 1 else note_str
        return cls(date=date, feedback=feedback)


class WikiArticle:
    """Одна wiki-статья темы: frontmatter (OKF v0.2) + тело markdown.

    notes — список структурированных записей WikiNote (dict в JSON).
    Каждая запись содержит: date, feedback, и опционально question, student_answer, correct_answer.
    """

    MAX_NOTES = 10  # максимальное количество заметок

    def __init__(
        self,
        subject: str,
        topic: str,
        title: Optional[str] = None,
        grade: Optional[str] = None,
        curriculum: Optional[str] = None,
        mastery: float = 0.5,
        attempts: int = 0,
        correct: int = 0,
        last_studied: Optional[str] = None,
        weak_areas: Optional[List[str]] = None,
        relations: Optional[List[Dict[str, str]]] = None,
        notes: Optional[List[Any]] = None,
        concepts: Optional[List[str]] = None,
        source: str = "",
        body: str = "",
        section_number: Optional[str] = None,
    ) -> None:
        self.subject = subject
        self.topic = topic
        self.title = title or topic
        self.grade = grade
        self.curriculum = curriculum
        self.mastery = round(float(mastery), 4)
        self.attempts = int(attempts)
        self.correct = int(correct)
        self.last_studied = last_studied or _now_iso()
        self.weak_areas = weak_areas or []
        self.relations = relations or []
        self._notes: List[WikiNote] = []
        # Нормализуем notes: принимаём List[str], List[Dict], или List[WikiNote]
        if notes:
            for n in notes:
                if isinstance(n, WikiNote):
                    self._notes.append(n)
                elif isinstance(n, dict):
                    self._notes.append(WikiNote.from_dict(n))
                elif isinstance(n, str):
                    # backwards compatibility: парсим старую строку
                    self._notes.append(WikiNote.from_legacy_string(n))
        self.concepts = concepts or []
        # Источник информации (URL страницы / имя учебника) — из RAG-чанков
        self.source = source or ""
        self.body = body
        self.section_number = section_number

    # --- сериализация ---
    @property
    def accuracy(self) -> float:
        return round(self.correct / self.attempts, 4) if self.attempts else 0.0

    def frontmatter(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "okf_version": OKF_VERSION,
            "type": "Topic",
            "title": self.title,
            "topic": self.topic,
            "subject": self.subject,
            "grade": self.grade or "",
            "curriculum": self.curriculum or "",
            "mastery": self.mastery,
            "accuracy": self.accuracy,
            "attempts": self.attempts,
            "correct": self.correct,
            "last_studied": self.last_studied,
        }
        if self.section_number:
            data["section_number"] = self.section_number
        if self.weak_areas:
            data["weak_areas"] = self.weak_areas
        if self.relations:
            data["relations"] = self.relations
        if self.notes:
            data["notes"] = self.notes
        if self.concepts:
            data["concepts"] = self.concepts
        if self.source:
            data["source"] = self.source
        return data

    def to_markdown(self) -> str:
        front = "---\n" + yaml.safe_dump(self.frontmatter(), allow_unicode=True, sort_keys=False) + "---\n"
        body = self.body.strip()
        if not body:
            body = f"Материал по теме «{self.title}» накапливается по мере прохождения квизов.\n"
        return front + f"# {self.title}\n\n{body}\n"
    @property
    def notes(self) -> List[Dict[str, Any]]:
        """Сериализованные заметки (List[Dict]) — для frontmatter/API."""
        return [n.to_dict() for n in self._notes]

    def _normalize_feedback(self, feedback: str) -> str:
        """Ограничение длины feedback для дедупликации."""
        return feedback[:180]

    def _find_or_merge_note(self, feedback_key: str) -> Optional[WikiNote]:
        """Найти существующую заметку с таким же feedback_key для группировки ошибок."""
        for note in self._notes:
            if note.feedback == feedback_key:
                return note
        return None

    def add_note(
        self,
        feedback: str,
        question: Optional[str] = None,
        student_answer: Optional[str] = None,
        correct_answer: Optional[str] = None,
    ) -> None:
        """Добавить структурированную заметку с дедупликацией и ограничением.

        - Дедупликация: если такая же ошибка уже есть — не добавляем.
        - Ограничение: храним только последние MAX_NOTES заметок.
        - Группировка: одинаковые feedback (без даты) группируются через счётчик в комментарии.
        """
        if not feedback:
            return

        normalized_fb = self._normalize_feedback(feedback)
        date = self.last_studied[:10] if self.last_studied else _now_iso()[:10]

        # Проверяем дедупликацию по feedback (игнорируя дату в сравнении)
        existing = self._find_or_merge_note(normalized_fb)
        if existing:
            # Уже есть такая ошибка — обновляем дату на самую свежую
            existing.date = date
            if not existing.student_answer and student_answer:
                existing.student_answer = student_answer
            if not existing.correct_answer and correct_answer:
                existing.correct_answer = correct_answer
            return

        note = WikiNote(
            date=date,
            feedback=normalized_fb,
            question=question,
            student_answer=student_answer,
            correct_answer=correct_answer,
        )
        self._notes.append(note)

        # Ограничиваем количество заметок — оставляем последние MAX_NOTES
        if len(self._notes) > self.MAX_NOTES:
            self._notes = self._notes[-self.MAX_NOTES:]

    def apply_result(
        self,
        topic: str,
        score01: float,
        correct: bool,
        feedback: str = "",
        question: Optional[str] = None,
        student_answer: Optional[str] = None,
        correct_answer: Optional[str] = None,
    ) -> None:
        """Экспоненциальное сглаживание мастерства + добавление структурированной заметки.

        При ошибке (correct=False) создаёт dict-запись с полными полями контекста:
        question, student_answer, feedback, correct_answer.
        """
        self.mastery = round(0.7 * self.mastery + 0.3 * float(score01), 4)
        self.attempts += 1
        if correct:
            self.correct += 1
        self.last_studied = _now_iso()
        if not correct and feedback:
            self.add_note(
                feedback=feedback,
                question=question,
                student_answer=student_answer,
                correct_answer=correct_answer,
            )

    def to_dict(self) -> Dict[str, Any]:
        """Полная сериализация статьи для API (frontmatter + body)."""
        d = self.frontmatter()
        d["body"] = self.body
        d["accuracy"] = self.accuracy
        return d

    @classmethod
    def from_dict(cls, subject: str, topic: str, data: Dict[str, Any]) -> "WikiArticle":
        raw_notes = data.get("notes")
        # Передаём raw_notes — __init__ сам нормализует (включая backwards compatibility)
        return cls(
            subject=str(data.get("subject") or subject),  # human-имя из frontmatter, не slug каталога
            topic=topic,
            title=data.get("title"),
            grade=data.get("grade"),
            curriculum=data.get("curriculum"),
            mastery=data.get("mastery", 0.5),
            attempts=data.get("attempts", 0),
            correct=data.get("correct", 0),
            last_studied=data.get("last_studied"),
            weak_areas=data.get("weak_areas"),
            relations=data.get("relations"),
            notes=raw_notes,
            concepts=data.get("concepts"),
            source=data.get("source") or "",
            body=data.get("body", ""),
            section_number=data.get("section_number"),
        )


class KnowledgeWiki:
    """Хранилище wiki-статей (персистентное, между сессиями)."""

    def __init__(self, root_dir: Optional[Any] = None) -> None:
        self.root = Path(root_dir or default_settings.KNOWLEDGE_WIKI_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- пути ---
    def subject_dir(self, subject: str) -> Path:
        d = self.root / _slug(subject)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def article_path(self, subject: str, topic: str) -> Path:
        return self.subject_dir(subject) / f"{_slug(topic)}.md"

    # --- чтение ---
    def get(self, subject: str, topic: str) -> Optional[WikiArticle]:
        p = self.article_path(subject, topic)
        if not p.exists():
            return None
        return self._read_file(p, subject, topic)

    def _read_file(self, path: Path, subject: str, topic: str) -> Optional[WikiArticle]:
        try:
            text = path.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) < 3:
                return None
            data = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
            # убрать маркдаун-заголовок из тела для чистоты
            lines = body.splitlines()
            if lines and lines[0].startswith("#"):
                body = "\n".join(lines[1:]).strip()
            real_topic = str(data.get("topic") or topic)
            art = WikiArticle.from_dict(subject, real_topic, {**data, "body": body})
            return art
        except Exception:
            return None

    def list_subjects(self) -> List[str]:
        out = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and d.name != "_index":
                # каталог без статей (файлы удалены) не показываем
                if any(f.suffix == ".md" and f.name != _INDEX_NAME for f in d.iterdir()):
                    out.append(d.name)
        return out

    def list_articles(self, subject: Optional[str] = None) -> List[WikiArticle]:
        articles = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or (subject and d.name != _slug(subject)):
                continue
            for f in sorted(d.glob("*.md")):
                if f.name == _INDEX_NAME:
                    continue
                art = self._read_file(f, d.name, f.stem)
                if art:
                    articles.append(art)
        return articles

    # --- запись ---
    def upsert(self, article: WikiArticle) -> Path:
        p = self.article_path(article.subject, article.topic)
        p.write_text(article.to_markdown(), encoding="utf-8")
        self._write_index(article.subject)
        return p

    def _write_index(self, subject: str) -> None:
        articles = [a for a in self.list_articles(subject)]
        if not articles:
            return
        lines = [f"# Предмет «{subject}»\n", "Темы и текущее мастерство:\n"]
        for a in sorted(articles, key=lambda x: -x.mastery):
            pct = int(round(a.mastery * 100))
            lines.append(f"- [{a.title}]({_slug(a.topic)}.md) — мастерство {pct}% (попыток: {a.attempts})")
        meta = {
            "okf_version": OKF_VERSION,
            "type": "Index",
            "title": f"Предмет «{subject}»",
            "subject": subject,
            "last_studied": _now_iso(),
        }
        (self.subject_dir(subject) / _INDEX_NAME).write_text(
            "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    # --- агрегация из сессии ---
    def apply_record(self, state: Any, record: Dict[str, Any]) -> Optional[WikiArticle]:
        """Применить ОДИН ответ сессии к статье (идемпотентно: attempts+=1 за вызов).

        Вызывается из evaluate_answer_node для текущего ответа — НЕ пересчитывает
        все records заново (иначе attempts растут квадратично).
        
        Record может содержать дополнительные поля: question, student_answer,
        correct_answer — они сохраняются в структурированных заметках.
        """
        subject = getattr(state, "subject", None) or "общая тема"
        topic = record.get("topic") or getattr(state, "topic", None)
        if not topic:
            return None
        score = record.get("score01")
        correct = record.get("correct")
        if score is None or correct is None:
            return None
        art = self.get(subject, topic)
        if art is None:
            art = WikiArticle(
                subject=subject,
                topic=topic,
                title=topic,
                grade=getattr(state, "grade", None),
                curriculum=getattr(state, "curriculum", None),
            )
        art.apply_result(
            topic,
            float(score),
            bool(correct),
            record.get("feedback") or "",
            question=record.get("question"),
            student_answer=record.get("student_answer"),
            correct_answer=record.get("correct_answer"),
        )
        self.upsert(art)
        return art

    def sync_mastery(self, state: Any) -> List[WikiArticle]:
        """Синхронизация mastery из knowledge_map (идемпотентно, без attempts++).

        Вызывается из summary_node при завершении квиза.
        """
        subject = getattr(state, "subject", None) or "общая тема"
        updated: List[WikiArticle] = []
        for topic, mastery in getattr(state, "knowledge_map", {}).items():
            if not topic:
                continue
            art = self.get(subject, topic)
            if art is None:
                art = WikiArticle(
                    subject=subject,
                    topic=topic,
                    title=topic,
                    grade=getattr(state, "grade", None),
                    curriculum=getattr(state, "curriculum", None),
                    mastery=mastery,
                )
            art.mastery = round(mastery, 4)
            art.last_studied = _now_iso()
            self.upsert(art)
            updated.append(art)
        return updated

    def update_from_session(self, state: Any) -> List[WikiArticle]:
        """Совместимость: sync_mastery (не пересчитывает attempts по records).

        Ранее применял ВСЕ records заново → attempts росли квадратично и база
        знаний выглядела «странно» (десятки попыток, 0 верных). Теперь попытки
        накапливаются только через apply_record на каждый ответ.
        """
        return self.sync_mastery(state)

    def enrich_body(
        self,
        state: Any,
        topic: str,
        context: List[str],
        llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    ) -> Optional[WikiArticle]:
        """Wiki-LLM (roadmap #2): генерирует/обновляет тело статьи фактами из RAG-контекста.

        Берёт тему, изученную в сессии, и RAG-чанки по ней → LLM пишет краткое
        «конспект-статью» (факты, термины). Записывается в body статьи.
        Если LLM недоступен/пустой результат — статья остаётся каркасом (не ломаем).
        """
        subject = getattr(state, "subject", None) or "общая тема"
        art = self.get(subject, topic)
        if art is None:
            art = WikiArticle(subject=subject, topic=topic, title=topic,
                              grade=getattr(state, "grade", None))
        chunks = [c for c in (context or []) if c and c.strip()]
        if not chunks:
            return art  # нет контекста — тело не обновляем

        if llm_call is None:
            return art

        try:
            system = (
                "Ты — Wiki-LLM EduTutor. По фрагментам учебных материалов напиши краткий "
                "конспект темы (3-6 предложений): ключевые факты, термины, определения. "
                "Верни ТОЛЬКО текст конспекта, без заголовков и списков."
            )
            user = f"Тема: {topic}\nФрагменты материалов:\n" + "\n---\n".join(chunks)[:4000]
            body = (llm_call([{"role": "system", "content": system},
                              {"role": "user", "content": user}]) or "").strip()
            body = body.strip(" \n\"'`")
            if body and len(body) > 20:
                art.body = body
                art.last_studied = _now_iso()
                self.upsert(art)
        except Exception:
            return art  # LLM недоступен — не роняем поток
        return art

    def set_source(self, state: Any, topic: str, source: str) -> Optional[WikiArticle]:
        """Источник информации (URL/учебник) для темы — из RAG-чанков, если ещё не задан."""
        source = (source or "").strip()
        if not source:
            return None
        subject = getattr(state, "subject", None) or "общая тема"
        art = self.get(subject, topic)
        if art is None:
            return None
        if not art.source:
            art.source = source
            self.upsert(art)
        return art

    def sync_concepts(self, state: Any, topic: str, concepts: List[str]) -> Optional[WikiArticle]:
        """Roadmap #3 (drill-down): ключевые понятия темы (словарик урока) → статья.

        Идемпотентно: понятия перезаписываются последним уроком; статья создаётся
        при необходимости (тема может быть изучена, но ещё не пройден квиз).
        """
        concepts = [str(c).strip() for c in (concepts or []) if str(c).strip()]
        if not concepts:
            return None
        subject = getattr(state, "subject", None) or "общая тема"
        art = self.get(subject, topic)
        if art is None:
            art = WikiArticle(subject=subject, topic=topic, title=topic,
                              grade=getattr(state, "grade", None))
        art.concepts = concepts
        art.last_studied = _now_iso()
        self.upsert(art)
        return art

    def to_summary_dict(self) -> List[Dict[str, Any]]:
        """Сводка для API: предмет (человеческое имя из статей) → темы с мастерством/датой."""
        out: List[Dict[str, Any]] = []
        for subject in self.list_subjects():
            items = self.list_articles(subject)
            display = items[0].subject if items else subject
            out.append({"subject": display, "articles": [a.to_dict() for a in items]})
        return out
