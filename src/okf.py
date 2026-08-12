"""
EduTutor — Open Knowledge Format (OKF v0.2) экспорт знаний учебника.

OKF (Google Cloud, июнь 2026): знания = каталог markdown-файлов с YAML-frontmatter;
концепция = файл, путь = ID; конформизм = непустой `type`.
Устраняет фрагментацию знаний учебника: граф (сессия+JSON), чанки (ChromaDB),
ФГОС, карта знаний — в единый переносимый бандл.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .knowledge_graph import KnowledgeGraph

OKF_VERSION = "0.2"
GENERATOR = "edututor/0.1"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _slug(text: str) -> str:
    """Имя файла из названия темы (латиница/цифры/дефис)."""
    out = []
    for ch in text.lower():
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        elif ch in " /\\":
            out.append("-")
    s = "".join(out).strip("-")
    return s or "topic"


def _frontmatter(data: Dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False) + "---\n"


def _node_relations(node_id: str, kg: KnowledgeGraph) -> List[Dict[str, Any]]:
    rels = []
    for e in kg.to_dict()["edges"]:
        if e["source"] == node_id:
            rels.append({"target": e["target"], "relation": e["relation"]})
    return rels


def emit_okf_bundle(
    state: Any,
    out_dir: Path,
    source_name: str,
    subject: Optional[str] = None,
    grade: Optional[str] = None,
    curriculum: Optional[str] = None,
) -> Path:
    """Эмитит OKF-бандл знаний учебника в out_dir (создаётся). Возвращает out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    kg = KnowledgeGraph.from_dict(state.knowledge_graph or {})
    generated = _now()
    subject = subject or getattr(state, "subject", None)
    grade = grade or getattr(state, "grade", None)
    curriculum = curriculum or getattr(state, "curriculum", None) or None

    # --- index.md ---
    index_meta = {
        "okf_version": OKF_VERSION,
        "type": "Index",
        "title": f"Учебник «{source_name}»",
        "subject": subject or "",
        "grade": grade or "",
        "curriculum": curriculum or "",
        "status": "stable",
        "generated": {"by": GENERATOR, "at": generated},
    }
    body = f"# Учебник «{source_name}»\n\nГраф знаний: {kg.stats()['nodes']} узлов, {kg.stats()['edges']} рёбер.\n\nТемы:\n"
    topics_dir = out_dir / "topics"
    topics_dir.mkdir(exist_ok=True)
    for n in kg.to_dict()["nodes"]:
        if n.get("type") == "book":
            continue
        body += f"- [«{n.get('title', '')}»](topics/{_slug(n.get('title', 'topic'))}.md)\n"
    body += "\nПроисхождение: распознано из структуры текста учебника.\n"
    (out_dir / "index.md").write_text(_frontmatter(index_meta) + body, encoding="utf-8")

    # --- log.md ---
    log_meta = {"type": "ChangeLog", "title": "История изменений"}
    log = f"## {generated}\n- Сформирован бандл знаний учебника «{source_name}» ({GENERATOR}).\n"
    (out_dir / "log.md").write_text(_frontmatter(log_meta) + log, encoding="utf-8")

    # --- topics/*.md ---
    for n in kg.to_dict()["nodes"]:
        if n.get("type") == "book":
            continue
        nid = n.get("id", "")
        title = n.get("title", "")
        meta = {
            "type": "Topic" if n.get("type") == "topic" else "Section",
            "title": title,
            "subject": subject or "",
            "grade": grade or "",
            "status": "stable",
            "generated": {"by": GENERATOR, "at": generated},
        }
        if n.get("section_number"):
            meta["section_number"] = n["section_number"]
        if curriculum:
            meta["curriculum"] = curriculum
        rels = _node_relations(nid, kg)
        if rels:
            meta["relations"] = rels
        if state.knowledge_map and title in state.knowledge_map:
            meta["mastery"] = state.knowledge_map[title]

        desc = f"{title}\n\nРаздел учебника «{source_name}»"
        if n.get("section_number"):
            desc += f", №{n['section_number']}"
        desc += ". Открыть и готовиться по теме: выбор узла в графе знаний.\n"
        (topics_dir / f"{_slug(title)}.md").write_text(_frontmatter(meta) + desc, encoding="utf-8")

    return out_dir


def validate_bundle(bundle_dir: Path) -> Dict[str, Any]:
    """Базовая проверка конформизма OKF: md-файлы с frontmatter и непустым type."""
    bundle_dir = Path(bundle_dir)
    errors: List[str] = []
    files: List[str] = []
    for p in sorted(bundle_dir.rglob("*.md")):
        rel = str(p.relative_to(bundle_dir)).replace("\\", "/")
        files.append(rel)
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---"):
            errors.append(f"{rel}: нет YAML-frontmatter")
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            errors.append(f"{rel}: frontmatter не закрыт")
            continue
        try:
            data = yaml.safe_load(parts[1])
        except Exception as e:
            errors.append(f"{rel}: YAML невалиден: {e}")
            continue
        if not isinstance(data, dict) or not str(data.get("type", "")).strip():
            errors.append(f"{rel}: поле type пустое")
    return {"conformant": not errors, "errors": errors, "files": files}
