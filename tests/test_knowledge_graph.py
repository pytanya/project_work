"""Тесты графа знаний учебника (подготовка по темам)."""

from __future__ import annotations

from pathlib import Path

from src.knowledge_graph import (
    KnowledgeGraph,
    PART_OF,
    PREREQUISITE,
    RELATED,
    build_or_load_textbook_graph,
    build_textbook_graph,
    clean_title,
    graph_cache_key,
    load_cached_graph,
    save_graph,
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

    def test_web_headings_build_topics(self):
        """Веб-конспект: markdown-заголовки → несколько узлов-тем (не один generic «topic»)."""
        text = (
            "# Иммануил Кант\n"
            "Введение в философию Канта.\n"
            "## Жизнь и биография\n"
            "Родился в Кёнигсберге.\n"
            "## Критика чистого разума\n"
            "Основной труд.\n"
            "### Трансцендентальная эстетика\n"
            "Учение о пространстве и времени.\n"
        )
        kg = build_textbook_graph(text, source="Кант")
        titles = [d["title"] for _, d in kg.graph.nodes(data=True)]
        assert "Жизнь и биография" in titles
        assert "Критика чистого разума" in titles
        assert "Трансцендентальная эстетика" in titles
        assert kg.graph.number_of_nodes() >= 4  # book + 3+ темы

    def test_web_headings_clean_entities_and_surrogates(self):
        """HTML-entities (&#8470;) и суррогаты не попадают в заголовки узлов."""
        text = (
            "# Параграф &#8470;&#160;43. Критика\n"
            "Текст.\n"
            "## Учение о &#171;вещи в себе&#187;\n"
            "Текст.\n"
            "## Философия \udc98ммануила Канта\n"
            "Текст.\n"
        )
        kg = build_textbook_graph(text, source="Кант")
        titles = [d["title"] for _, d in kg.graph.nodes(data=True)]
        assert "Параграф № 43. Критика" in titles or "Параграф № 43" in "|".join(titles)
        assert "Учение о «вещи в себе»" in titles
        # суррогат удалён, повреждённая буква потеряна, но слово осталось читаемым
        assert not any("\udc98" in t for t in titles)
        assert "Канта" in "|".join(titles)

    def test_clean_title(self):
        assert clean_title("Тема &#8470;&#160;1") == "Тема № 1"
        assert clean_title("Философия \udc98ммануила") == "Философия ммануила"
        assert clean_title("  # Просто текст  ") == "Просто текст"
        assert clean_title("") == ""

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


class TestCache:
    def test_key_stable_for_same_file(self, tmp_path: Path):
        f = tmp_path / "book.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        k1 = graph_cache_key("book.pdf", f)
        k2 = graph_cache_key("book.pdf", f)
        assert k1 == k2

    def test_key_changes_when_file_changes(self, tmp_path: Path):
        f = tmp_path / "book.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        k1 = graph_cache_key("book.pdf", f)
        f.write_bytes(b"%PDF-1.4 fake longer content changed")
        k2 = graph_cache_key("book.pdf", f)
        assert k1 != k2

    def test_build_or_load_saves_and_reuses(self, tmp_path: Path):
        f = tmp_path / "book.pdf"
        f.write_bytes(b"data")
        key = graph_cache_key("book", f)

        kg1 = build_or_load_textbook_graph(OPK_TEXT, source="book", path=f, graph_dir=tmp_path / "g")
        cache_path = tmp_path / "g" / f"{key}.json"
        assert cache_path.exists()
        assert load_cached_graph(key, tmp_path / "g") is not None

        # повторный вызов с тем же файлом → грузим из кэша (тот же граф)
        kg2 = build_or_load_textbook_graph(OPK_TEXT, source="book", path=f, graph_dir=tmp_path / "g")
        assert kg2.stats() == kg1.stats()

    def test_no_cache_when_path_none(self, tmp_path: Path):
        kg = build_or_load_textbook_graph(OPK_TEXT, source="book", path=None, graph_dir=tmp_path / "g")
        assert kg.stats()["nodes"] >= 2  # строится, но не кэшируется
        assert not list((tmp_path / "g").glob("*.json"))

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        kg = build_textbook_graph(OPK_TEXT, source="opk")
        p = save_graph("k1", kg, tmp_path)
        assert p.exists()
        loaded = load_cached_graph("k1", tmp_path)
        assert loaded is not None
        assert loaded.stats() == kg.stats()


class TestModelOntology:
    """Модель строит онтологию (вершины+рёбра) из контента; эвристика — fallback."""

    _GOOD = (
        '{"nodes": [{"id": "c1", "title": "Атмосфера", "type": "topic", "section": "§12"}, '
        '{"id": "c2", "title": "Состав атмосферы", "type": "concept", "section": "§12"}, '
        '{"id": "c3", "title": "Давление воздуха", "type": "concept", "section": "§13"}], '
        '"edges": [{"source": "c1", "target": "c2", "relation": "part_of"}, '
        '{"source": "c2", "target": "c3", "relation": "prerequisite"}, '
        '{"source": "c1", "target": "нет_такого", "relation": "related"}]}'
    )

    def test_model_builds_ontology(self):
        from src.knowledge_graph import build_model_graph

        kg = build_model_graph("текст про атмосферу", "geog", lambda m: self._GOOD)
        assert kg is not None
        nodes = kg.to_dict()["nodes"]
        titles = [n["title"] for n in nodes]
        assert "Атмосфера" in titles
        assert "Состав атмосферы" in titles
        assert any(n.get("type") == "concept" for n in nodes)
        # рёбра: валидные есть, битые (нет_такого) отфильтрованы
        edges = kg.to_dict()["edges"]
        rels = {(e["source"], e["target"], e["relation"]) for e in edges}
        assert ("c1", "c2", PART_OF) in rels
        assert ("c2", "c3", PREREQUISITE) in rels
        assert not any("нет_такого" in (e["source"] + e["target"]) for e in edges)
        # section нормализован «§12» → «12» для RAG-фильтра
        sec12 = [n for n in nodes if n.get("title") == "Состав атмосферы"][0]
        assert sec12.get("section_number") == "12"

    def test_model_garbage_returns_none(self):
        from src.knowledge_graph import build_model_graph

        assert build_model_graph("t", "s", lambda m: "не json") is None
        assert build_model_graph("t", "s", lambda m: '{"nodes": []}') is None
        assert build_model_graph("t", "s", None) is None

    def test_model_llm_error_falls_back(self):
        from src.knowledge_graph import build_model_graph

        def boom(m):
            raise RuntimeError("LLM недоступен")

        assert build_model_graph("t", "s", boom) is None

    def test_build_or_load_prefers_model_then_fallback(self, tmp_path: Path):
        f = tmp_path / "book.pdf"
        f.write_bytes(b"data")

        # модель работает → граф модели
        kg = build_or_load_textbook_graph(OPK_TEXT, source="book", path=f,
                                          graph_dir=tmp_path / "g", llm_ontology=lambda m: self._GOOD)
        titles = [n["title"] for n in kg.to_dict()["nodes"]]
        assert "Состав атмосферы" in titles  # понятие от модели

        # модель падает → эвристический каркас (Уроки/Параграф)
        kg2 = build_or_load_textbook_graph(OPK_TEXT, source="book2", path=f,
                                           graph_dir=tmp_path / "g",
                                           llm_ontology=lambda m: (_ for _ in ()).throw(RuntimeError("down")))
        titles2 = [n["title"] for n in kg2.to_dict()["nodes"]]
        assert any("Урок" in t for t in titles2)

    def test_model_undefined_relations_become_related(self):
        from src.knowledge_graph import build_model_graph

        raw = ('{"nodes": [{"id": "a", "title": "Понятие А"}, {"id": "b", "title": "Понятие Б"}], '
               '"edges": [{"source": "a", "target": "b", "relation": "explains"}]}')
        kg = build_model_graph("t", "s", lambda m: raw)
        assert kg is not None
        edges = kg.to_dict()["edges"]
        ab = [e for e in edges if e["source"] == "a" and e["target"] == "b"]
        assert ab and ab[0]["relation"] == RELATED
