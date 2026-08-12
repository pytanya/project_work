"""Тесты графа знаний учебника (подготовка по темам)."""

from __future__ import annotations

from src.knowledge_graph import (
    KnowledgeGraph,
    PART_OF,
    PREREQUISITE,
    RELATED,
    build_textbook_graph,
)

OPK_TEXT = (
    "Урок 1: Россия — наша Родина\nСодержание первого урока.\n\n"
    "Урок 2. Культура и религия\nСодержание второго урока.\n\n"
    "Параграф 5. Традиции\nСодержание параграфа.\n"
)


class TestBuild:
    def test_builds_sections_and_hierarchy(self):
        kg = build_textbook_graph(OPK_TEXT, source="opk.pdf")
        assert kg.graph.number_of_nodes() == 4  # book + 3 секции
        edges = list(kg.graph.edges(data=True))
        assert len(edges) == 3  # все part_of от корня
        assert all(d.get("relation") == PART_OF for _, _, d in edges)

    def test_no_sections_falls_back_to_topic(self):
        kg = build_textbook_graph("Просто текст без заголовков.", source="x")
        assert kg.graph.number_of_nodes() == 2  # book + topic

    def test_running_header_lessons_detected(self):
        # колонтитулы вида «4 Урок 1 ОСНОВЫ…» (не начало строки) — ловим номера
        text = "мир может радоваться или 4 Урок 1 ОСНОВЫ текст\nзатем 6 Урок 2 ОСНОВЫ текст\n8 Урок 1 повторился"
        kg = build_textbook_graph(text, source="opk")
        titles = [d["title"] for _, d in kg.graph.nodes(data=True)]
        assert "Урок 1" in titles
        assert "Урок 2" in titles
        assert kg.graph.number_of_nodes() >= 3  # book + 2 урока

    def test_llm_prerequisite_links(self):
        def fake_llm(ids):
            return [{"source": ids[1], "target": ids[0], "relation": PREREQUISITE}]

        kg = build_textbook_graph(OPK_TEXT, source="opk", llm_link=fake_llm)
        rels = [d.get("relation") for _, _, d in kg.graph.edges(data=True)]
        assert PREREQUISITE in rels


class TestSearch:
    def test_neighbors_dfs(self):
        kg = build_textbook_graph(OPK_TEXT, source="opk")
        root = [n for n in kg.graph.nodes if kg.graph.nodes[n].get("type") == "book"][0]
        neighbors = kg.neighbors(root, max_depth=2)
        assert len(neighbors) == 3  # три секции от корня
        assert all(n["relation"] == PART_OF for n in neighbors)

    def test_find_path(self):
        kg = KnowledgeGraph()
        kg.add_topic("a", "A", "topic")
        kg.add_topic("b", "B", "topic")
        kg.add_topic("c", "C", "topic")
        kg.add_edge("a", "b", PREREQUISITE)
        kg.add_edge("b", "c", PREREQUISITE)
        path = kg.find_path("a", "c")
        assert [e["target"] for e in path] == ["b", "c"]
        assert path[0]["relation"] == PREREQUISITE

    def test_search_by_keyword(self):
        kg = build_textbook_graph(OPK_TEXT, source="opk")
        hits = kg.search("Культура")
        assert len(hits) >= 1


class TestSerialization:
    def test_roundtrip(self):
        kg = build_textbook_graph(OPK_TEXT, source="opk")
        data = kg.to_dict()
        kg2 = KnowledgeGraph.from_dict(data)
        assert kg2.graph.number_of_nodes() == kg.graph.number_of_nodes()
        assert kg2.graph.number_of_edges() == kg.graph.number_of_edges()
        assert kg2.stats()["nodes"] >= 2

    def test_node_colors_present(self):
        kg = build_textbook_graph(OPK_TEXT, source="opk")
        for n, d in kg.graph.nodes(data=True):
            assert d.get("color")
