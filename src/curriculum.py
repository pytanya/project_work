"""
EduTutor — сверка с ФГОС (grade_curriculum, В-8).

- load_fgos_reference: загрузка офлайн-базы (JSON/YAML) из FGOS_REFERENCE_DIR.
- lookup_fgos: маппинг тема/раздел → код/раздел ФГОС по ключевым словам.
- grade_curriculum: полная сверка + честный fallback «не проверено по ФГОС».
- collect_fgos_via_crawl4ai: дополнение базы из открытых источников Plan A (В-8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from .config import settings as default_settings

SUBJECT_SYNONYMS = {
    "geography": ["география", "геогр"],
    "history": ["история", "истор"],
    "biology": ["биология", "биол"],
    "physics": ["физика", "физ"],
    "chemistry": ["химия"],
    "math": ["математика", "алгебра", "геометрия", "мат"],
    "russian": ["русский"],
    "literature": ["литература", "лит"],
}


@dataclass
class CurriculumResult:
    """Результат сверки темы с ФГОС."""

    fgos_code: Optional[str] = None
    status: Literal["matched", "not_found", "reference_unavailable"] = "reference_unavailable"
    warning: str = ""
    reference_subject: Optional[str] = None


def _load_file(path: Path) -> Dict[str, Any]:
    """Загружает JSON-файл базы (или YAML, если доступен PyYAML)."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # noqa: WPS433 (опциональная зависимость)

            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except ImportError:
            return {}
    return {}


def load_fgos_reference(ref_dir: Path) -> Dict[str, Any]:
    """Загрузка офлайн-базы ФГОС из каталога (объединение всех JSON/YAML).

    Возвращает {subject: {grade: [{"topics": [...], "code": ...}]}}.
    """
    reference: Dict[str, Any] = {}
    if not ref_dir.exists():
        return reference
    for path in sorted(ref_dir.iterdir()):
        if path.suffix.lower() not in (".json", ".yaml", ".yml"):
            continue
        data = _load_file(path)
        if not isinstance(data, dict):
            continue
        for subject, grades in data.items():
            reference.setdefault(subject, {}).update(grades or {})
    return reference


def _grade_matches(key: str, grade: Optional[str]) -> bool:
    if not grade:
        return False
    key = key.strip()
    if key == grade:
        return True
    if "-" in key:
        try:
            a, b = key.split("-")
            return int(a) <= int(grade) <= int(b)
        except ValueError:
            return False
    return False


def _resolve_subject(subject: Optional[str]) -> str:
    """Приводит предмет к ключу справочника по синонимам."""
    s = (subject or "").lower()
    for canonical, synonyms in SUBJECT_SYNONYMS.items():
        for syn in synonyms:
            if syn in s:
                return canonical
    return s


def lookup_fgos(reference: Dict[str, Any], subject: str, grade: str, topic: str) -> Optional[str]:
    """Поиск кода ФГОС для темы/раздела по офлайн-базе.

    Совпадение по ключевым словам: ключевое слово в теме или тема в ключевом слове.
    """
    canonical = _resolve_subject(subject)
    if canonical not in reference:
        return None
    grades = reference[canonical]
    topic_low = (topic or "").lower().strip()
    for grade_key, entries in grades.items():
        if not _grade_matches(grade_key, grade):
            continue
        for entry in entries or []:
            for kw in entry.get("topics", []):
                kw_low = kw.lower()
                if kw_low in topic_low or topic_low in kw_low:
                    return entry.get("code")
    return None


def grade_curriculum(
    subject: Optional[str],
    grade: Optional[str],
    topic: Optional[str],
    reference: Optional[Dict[str, Any]] = None,
    llm_match: Optional[Any] = None,
    ref_dir: Optional[Path] = None,
) -> CurriculumResult:
    """Сверка темы/раздела с ФГОС (В-8).

    Порядок: 1) офлайн-база; 2) LLM-сверка (опционально); 3) честный fallback
    «не проверено по ФГОС», если источника нет или тема не найдена.
    """
    if reference is None:
        reference = load_fgos_reference(ref_dir or default_settings.FGOS_REFERENCE_DIR)

    if not reference:
        return CurriculumResult(
            status="reference_unavailable",
            warning="Тема не проверена по ФГОС: справочник ФГОС недоступен.",
        )

    code = lookup_fgos(reference, subject or "", grade or "", topic or "")
    canonical = _resolve_subject(subject)

    if code:
        return CurriculumResult(
            fgos_code=code,
            status="matched",
            reference_subject=canonical,
        )

    if llm_match is not None:
        try:
            llm_code = llm_match(subject, grade, topic)
        except Exception:
            llm_code = None
        if isinstance(llm_code, str) and llm_code.strip():
            return CurriculumResult(
                fgos_code=llm_code.strip(),
                status="matched",
                reference_subject=canonical,
            )

    return CurriculumResult(
        status="not_found",
        warning=f"Тема «{topic or '—'}» не проверена по ФГОС: раздел не найден в справочнике.",
    )


# ----------------------------------------------------------------------
# Сбор базы через crawl4ai (В-8, решение заказчика)
# ----------------------------------------------------------------------
async def collect_fgos_via_crawl4ai(
    subject: str,
    grade: str,
    out_dir: Optional[Path] = None,
    settings: Any = None,
) -> Tuple[Path, List[str]]:
    """Дополнение офлайн-базы ФГОС из открытых источников Plan A (crawl4ai).

    Lazy-импорт crawl4ai (ставится на Слайсе 5). Извлекает заголовки разделов
    (потенциальные темы) со страниц Викиучебника/открытых образовательных сайтов
    и сохраняет черновик базы {subject: {grade: [{topics, code: null}]}}.
    Коды ФГОС заполняются человеком/LLM-сверкой на Этапе 1 (честный fallback).

    Returns:
        (путь к сохранённому файлу, список извлечённых тем)
    """
    import re

    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig  # noqa: WPS433

    s = settings or default_settings
    out_dir = out_dir or s.FGOS_REFERENCE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    url = f"https://ru.wikibooks.org/wiki/{subject.capitalize()}"
    topics: List[str] = []
    browser_config = BrowserConfig(headless=s.CRAWL4AI_PLAYWRIGHT_HEADLESS, verbose=False)
    run_config = CrawlerRunConfig(
        page_timeout=s.CRAWL4AI_PLAYWRIGHT_TIMEOUT_MS,
        check_robots_txt=s.CRAWL4AI_RESPECT_ROBOTS,
        verbose=False,
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        markdown = getattr(result, "markdown", "") or ""
        # Заголовки разделов как потенциальные темы учебной программы
        for match in re.finditer(r"^#{2,4}\s+(.+)$", markdown, re.MULTILINE):
            heading = match.group(1).strip()
            if len(heading) > 2 and len(heading) < 120:
                topics.append(heading)

    out_file = out_dir / f"{_resolve_subject(subject)}_{grade}.json"
    base: Dict[str, Any] = {}
    if out_file.exists():
        base = _load_file(out_file)
    base.setdefault(_resolve_subject(subject), {}).setdefault(grade, [])
    existing = base[_resolve_subject(subject)][grade]
    existing_topics = {kw.lower() for e in existing for kw in e.get("topics", [])}
    for t in topics:
        if t.lower() not in existing_topics:
            existing.append({"topics": [t], "code": None})
    out_file.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file, topics
