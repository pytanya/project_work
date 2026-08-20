"""
EduTutor — реестр инструментов (function calling, раздел 7.2).

Реестр TOOL_FUNCTIONS + execute_tool (по образцу research_guard_agent).
Инструменты: search_web, fetch_url, fetch_html, crawl_page_js,
download_file, verify_textbook, process_document, rag_search,
classify_intent, extract_entities, save_progress.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from .knowledge import (
    detect_page_offset,
    detect_text_layer,
    ocr_pages,
    pdf_page_count,
    process_document,
    validate_topic_in_text,
)
from .nlp import classify_intent, extract_entities, parse_doc_request
from .intake import extract_intake_fields
from .source_finder import (
    crawl_page_js,
    download_file,
    fetch_html,
    fetch_url,
    search_web,
    verify_textbook,
)


def rag_search(query: str, store: Any = None, k: int = 5) -> str:
    """Семантический поиск по коллекции (требует store в kwargs). Возвращает JSON."""
    if store is None:
        return json.dumps({"ok": False, "error": "store не передан"}, ensure_ascii=False)
    try:
        results = store.search(query, k=k)
        return json.dumps(
            {
                "ok": True,
                "results": [
                    {
                        "text": r.chunk.text[:400],
                        "section": r.chunk.section_number,
                        "source": r.chunk.source,
                        "score": round(r.score, 4),
                    }
                    for r in results
                ],
            },
            ensure_ascii=False,
        )
    except Exception as e:  # pragma: no cover
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


def save_progress(data: Any = None) -> str:
    """Заглушка сохранения прогресса обучаемого (после каждого ответа)."""
    return "Прогресс сохранён"


# Имя -> (функция, описание). Инструменты crawl_textbook_catalog и process_document
# сконфигурированы в графе; здесь — их исполняемые варианты.
TOOL_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "search_web": search_web,
    "fetch_url": fetch_url,
    "fetch_html": fetch_html,
    "crawl_page_js": crawl_page_js,
    "download_file": download_file,
    "verify_textbook": verify_textbook,
    "process_document": process_document,
    "rag_search": rag_search,
    "classify_intent": classify_intent,
    "extract_entities": extract_entities,
    "save_progress": save_progress,
    # Intake-инструменты (5.4, function calling): свободный ответ → поля чек-листа
    "extract_intake_fields": extract_intake_fields,
    # Сканированные учебники (3.2)
    "detect_text_layer": detect_text_layer,
    "parse_doc_request": parse_doc_request,
    "pdf_page_count": pdf_page_count,
    "detect_page_offset": detect_page_offset,
    "validate_topic_in_text": validate_topic_in_text,
    "ocr_pages": ocr_pages,
}


def _json_default(obj: Any) -> Any:
    """Дефолтный сериализатор JSON для dataclass/моделей."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(obj)
    return str(obj)


def execute_tool(name: str, arguments: str = "{}", **context: Any) -> str:
    """Выполнение инструмента по имени и JSON-аргументам.

    context — инжектируемые зависимости (store и т.п.), не сериализуются в ответ.
    Возвращает строку-результат (JSON или текст).
    """
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"ok": False, "error": f"Неизвестный инструмент: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        args = {}
    try:
        if name == "crawl_page_js":
            import asyncio

            result = asyncio.run(fn(args.get("url", "")))
            return json.dumps(result, ensure_ascii=False)
        result = fn(**args, **context)
        if isinstance(result, tuple):
            text, status = result
            return json.dumps({"ok": status == "OK", "status": status, "content": text}, ensure_ascii=False)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=_json_default)
    except TypeError as e:
        return json.dumps({"ok": False, "error": f"Неверные аргументы: {e}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
