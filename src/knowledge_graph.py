"""
EduTutor — граф знаний учебника (подготовка по темам).

По образцу GraphStore из hybrid-rag-project (NetworkX DiGraph):
узлы — темы/уроки/параграфы, рёбра — связи с типами
("входит в" part_of, "опирается на" prerequisite, "связан" related).
Обход DFS (соседи) и кратчайший путь — как в референсе.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import networkx as nx

from .config import settings as default_settings

# Типы связей
PART_OF = "part_of"
PREREQUISITE = "prerequisite"
RELATED = "related"

# Мульти-акцентная палитра для типов узлов (из hybrid-rag: gold/cyan/violet/green/pink)
NODE_COLORS = {
    "book": "#F4A261",
    "lesson": "#64DFDF",
    "section": "#B388FF",
    "topic": "#69F0AE",
    "default": "#FF8A80",
}

# Слова-маркеры уроков (вкл. mojibake «Óðîê» = «Урок» при cp1251-шрифте без ToUnicode)
_LESSON_WORD = r"(?:Урок|урок|Óðîê|Óðîê|Lesson|lesson|Module|module|Unit|unit|Параграф|параграф)"
_LESSON_TOC_RE = re.compile(
    rf"{_LESSON_WORD}\s*(\d{{1,3}})\s*\.\s*([А-ЯЁA-Za-zА-Яа-я][^.\n]{{2,90}}?)"
    rf"(?=\s*(?:[.·…]\s*){{3,}}|\s+\d+\s*$)",
    re.IGNORECASE,
)


def _lesson_titles(text: str) -> Dict[str, str]:
    """Извлекает {номер урока: название} из оглавления учебника.

    «Содержание: Урок 1. Россия — наша Родина . . . . 4». Слово «Урок» может быть
    в mojibake (cp1251-шрифт), название — чистое; отсекаем точечные-лидеры «...».
    """
    m = re.search(r"(?i)(содержание|оглавление)", text)
    region = text[m.end() : m.end() + 6000] if m else text[:6000]
    titles: Dict[str, str] = {}
    for num, title in _LESSON_TOC_RE.findall(region):
        title = re.split(r"\s*[.·…]{3,}", title.strip())[0].strip()
        titles[num] = title
    return titles


def _normalize_lesson_label(word: str) -> str:
    w = word.lower()
    if w in ("óðîê", "óðîê"):
        return "урок"
    return w


class KnowledgeGraph:
    """Граф знаний учебника на NetworkX DiGraph."""

    def __init__(self) -> None:
        self.graph = nx.DiGraph()

    # --- построение ---
    def add_topic(
        self,
        node_id: str,
        title: str,
        node_type: str = "topic",
        section_number: Optional[str] = None,
        **attrs: Any,
    ) -> None:
        data = dict(attrs)
        data.update(
            {"id": node_id, "title": title, "type": node_type, "color": NODE_COLORS.get(node_type, NODE_COLORS["default"])}
        )
        if section_number:
            data["section_number"] = section_number
        self.graph.add_node(node_id, **data)

    def add_edge(self, source: str, target: str, relation: str = RELATED) -> None:
        if source in self.graph and target in self.graph and source != target:
            self.graph.add_edge(source, target, relation=relation)

    # --- поиск (как в hybrid-rag GraphStore) ---
    def neighbors(self, node_id: str, max_depth: int = 2) -> List[Dict[str, Any]]:
        """DFS-обход: соседи сущности с типами связей."""
        if node_id not in self.graph:
            return []
        visited: set = set()
        results: List[Dict[str, Any]] = []

        def _traverse(current: str, depth: int, path: List[str]) -> None:
            if depth > max_depth or current in visited:
                return
            visited.add(current)
            for neighbor in self.graph.neighbors(current):
                edge = self.graph.get_edge_data(current, neighbor)
                rel = edge.get("relation", RELATED) if edge else RELATED
                results.append({
                    "source": current,
                    "source_title": self.graph.nodes[current].get("title", current),
                    "relation": rel,
                    "target": neighbor,
                    "target_title": self.graph.nodes[neighbor].get("title", neighbor),
                    "target_type": self.graph.nodes[neighbor].get("type", ""),
                    "depth": depth,
                })
                _traverse(neighbor, depth + 1, path + [neighbor])

        _traverse(node_id, 1, [node_id])
        return results

    def find_path(self, source_id: str, target_id: str) -> List[Dict[str, Any]]:
        """Кратчайший путь между двумя узлами."""
        if source_id not in self.graph or target_id not in self.graph:
            return []
        try:
            path = nx.shortest_path(self.graph, source_id, target_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
        edges: List[Dict[str, Any]] = []
        for i in range(len(path) - 1):
            s, t = path[i], path[i + 1]
            edge = self.graph.get_edge_data(s, t)
            edges.append({
                "source": s,
                "source_title": self.graph.nodes[s].get("title", s),
                "relation": edge.get("relation", RELATED) if edge else RELATED,
                "target": t,
                "target_title": self.graph.nodes[t].get("title", t),
            })
        return edges

    def search(self, query: str) -> List[str]:
        """Поиск узлов по названию (ключевые слова)."""
        q = query.lower()
        return [
            n for n, d in self.graph.nodes(data=True)
            if q in d.get("title", "").lower()
        ]

    # --- сериализация ---
    def to_dict(self) -> Dict[str, Any]:
        nodes = [
            {k: v for k, v in dict(d).items() if k in ("id", "title", "type", "color", "section_number")}
            for n, d in self.graph.nodes(data=True)
        ]
        edges = [
            {"source": s, "target": t, "relation": d.get("relation", RELATED)}
            for s, t, d in self.graph.edges(data=True)
        ]
        return {"nodes": nodes, "edges": edges}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "KnowledgeGraph":
        kg = cls()
        for n in (data or {}).get("nodes", []):
            kg.add_topic(
                n.get("id", ""), n.get("title", ""), n.get("type", "topic"),
                section_number=n.get("section_number"),
            )
        for e in (data or {}).get("edges", []):
            kg.add_edge(e.get("source", ""), e.get("target", ""), e.get("relation", RELATED))
        return kg

    def stats(self) -> Dict[str, int]:
        return {"nodes": self.graph.number_of_nodes(), "edges": self.graph.number_of_edges()}


# ----------------------------------------------------------------------
# Персистентный кэш графа (per-textbook, переживает сессии)
# ----------------------------------------------------------------------
GRAPH_SCHEMA_VERSION = 2  # bump при изменении структуры графа → пересборка кэша


def graph_cache_key(source_name: str, path: Optional[Any] = None) -> str:
    """Ключ кэша: схема графа + исходное имя учебника + размер файла.

    Схема входит в ключ — при изменении структуры графа старый кэш не
    подставляется (заголовки/узлы могли поменяться). НЕ используем mtime:
    при повторной загрузке temp-файл создаётся заново с другим mtime → ключ
    бы «прыгал». Размер отделяет разные учебники с одним именем.
    """
    fingerprint = f"v{GRAPH_SCHEMA_VERSION}"
    if path is not None:
        try:
            fingerprint += f":{source_name}:{Path(path).stat().st_size}"
        except OSError:
            fingerprint += f":{source_name}"
    else:
        fingerprint += f":{source_name}"
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def load_cached_graph(key: str, graph_dir: Optional[Any] = None) -> Optional[KnowledgeGraph]:
    """Загружает граф из кэша (data/knowledge_graphs/<key>.json) или None."""
    graph_dir = Path(graph_dir or default_settings.KNOWLEDGE_GRAPH_DIR)
    p = graph_dir / f"{key}.json"
    if not p.exists():
        return None
    try:
        return KnowledgeGraph.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def save_graph(key: str, kg: KnowledgeGraph, graph_dir: Optional[Any] = None) -> Path:
    """Сохраняет граф в дисковый кэш."""
    graph_dir = Path(graph_dir or default_settings.KNOWLEDGE_GRAPH_DIR)
    graph_dir.mkdir(parents=True, exist_ok=True)
    p = graph_dir / f"{key}.json"
    p.write_text(json.dumps(kg.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def build_or_load_textbook_graph(
    text: str,
    source: str,
    path: Optional[Any] = None,
    graph_dir: Optional[Any] = None,
    llm_link: Optional[Callable[[List[str]], List[Dict[str, str]]]] = None,
) -> KnowledgeGraph:
    """Build-once: граф из кэша (по fingerprint файла) или пересборка + сохранение."""
    if path is not None:
        key = graph_cache_key(source, path)
        cached = load_cached_graph(key, graph_dir)
        if cached is not None:
            return cached
    kg = build_textbook_graph(text, source, llm_link=llm_link)
    if path is not None:
        save_graph(key, kg, graph_dir)
    return kg


def build_textbook_graph(
    text: str,
    source: str,
    llm_link: Optional[Callable[[List[str]], List[Dict[str, str]]]] = None,
) -> KnowledgeGraph:
    """Строит граф знаний учебника из его структуры (уроки/параграфы).

    Узлы: корневой «учебник» + секции (Урок/Параграф/Module/...).
    Рёбра: иерархия part_of; опционально — LLM-связи prerequisite между уроками.
    Если секции не определены на старте строк (колонтитулы типа «4 Урок 1 …»),
    ловим номера уроков/параграфов в тексте лёгким regex.
    """
    import re

    from .knowledge import extract_sections

    kg = KnowledgeGraph()
    root_id = f"book:{source}"
    kg.add_topic(root_id, f"Учебник «{source}»", node_type="book")

    sections = extract_sections(text)
    section_ids: List[str] = []
    for label, num, title, _content in sections:
        nid = f"sec:{source}:{num}"
        kg.add_topic(nid, f"{label.capitalize()} {num}" + (f": {title}" if title else ""),
                     node_type="section", section_number=num)
        kg.add_edge(root_id, nid, PART_OF)
        section_ids.append(nid)

    if not section_ids:
        # колонтитулы «NN Урок M …» / «Урок M …» / «Параграф N …» в любом месте текста
        titles = _lesson_titles(text)  # «Урок N. Название» из оглавления
        found: List[str] = []
        word_re = re.compile(rf"(?i)\b({_LESSON_WORD})\s*(\d{{1,3}})")
        for m in word_re.finditer(text):
            label = _normalize_lesson_label(m.group(1))
            num = m.group(2)
            key = f"{label}:{num}"
            if key not in found:
                found.append(key)
                nid = f"sec:{source}:{num}"
                lesson_title = titles.get(num)
                node_title = f"{label.capitalize()} {num}"
                if lesson_title:
                    node_title += f": {lesson_title}"
                kg.add_topic(nid, node_title, node_type="topic")
                kg.add_edge(root_id, nid, PART_OF)
                section_ids.append(nid)

    if not section_ids:
        # нет структуры — один узел-«тема» от источника
        kg.add_topic(f"topic:{source}", f"Тема «{source}»", node_type="topic")
        kg.add_edge(root_id, f"topic:{source}", PART_OF)

    # LLM-связи «опирается на» (prerequisite) — опционально, по номерам секций
    if llm_link is not None and len(section_ids) >= 2:
        try:
            links = llm_link([s for s in section_ids]) or []
            for link in links:
                src = link.get("source", "")
                tgt = link.get("target", "")
                if src in kg.graph and tgt in kg.graph:
                    kg.add_edge(src, tgt, PREREQUISITE)
        except Exception:
            pass

    return kg
