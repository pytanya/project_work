"""Тесты RAG (Слайс 4): чанкинг, очистка, ChromaDB, embeddings, parse. """

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from src.config import BASE_DIR, Settings
from src.knowledge import (
    ApiEmbedder,
    DocChunk,
    HybridVectorStore,
    NumpyVectorStore,
    OkapiBM25,
    SearchResult,
    _consistent_offset,
    _extract_printed_number,
    clean_pdf_text,
    detect_text_layer,
    extract_sections,
    make_embedder,
    make_store,
    ocr_pages,
    parse_document,
    parse_pdf,
    process_document,
    rrf_merge,
    validate_topic_in_text,
)


class FakeEmbedder:
    """Детерминированный эмбеддер для тестов (8-dim по хэшам токенов)."""

    def __init__(self, model: str = "test"):
        self.model = model

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * 8
        for token in text.lower().split():
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:4], 16)
            v[h % 8] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def encode(self, texts):
        return [self._vec(t) for t in texts]

    def encode_query(self, text):
        return self._vec(text)


class TestCleanPdfText:
    def test_hyphenation(self):
        out = clean_pdf_text("Атмосфе-\nра — обо-\nлочка Земли.")
        assert "Атмосфера" in out
        assert "оболочка" in out

    def test_line_join_and_spaces(self):
        out = clean_pdf_text("Строение\nатмосферы.   Много\nпробелов.")
        assert "  " not in out
        assert out.startswith("Строение атмосферы.")

    def test_empty(self):
        assert clean_pdf_text("") == ""


class TestExtractSections:
    def test_parses_sections(self):
        text = (
            "Параграф 12: Атмосфера\nСтроение атмосферы.\n\n"
            "§13. Погода и климат\nПогода меняется.\n"
        )
        sections = extract_sections(text)
        assert len(sections) == 2
        label, num, title, content = sections[0]
        assert label == "параграф"
        assert num == "12"
        assert title == "Атмосфера"
        assert "Строение атмосферы" in content

    def test_lesson_sections(self):
        text = "Урок 1: Россия — наша Родина\nСодержание урока.\n\nУрок 2. Культура и религия\nЕщё текст.\n"
        sections = extract_sections(text)
        assert len(sections) == 2
        assert sections[0][0] == "урок"
        assert sections[0][1] == "1"

    def test_english_sections(self):
        text = "Module 1: Family\nSome content.\n\nUnit 2: School\nMore.\n"
        sections = extract_sections(text)
        assert len(sections) == 2
        assert sections[0][0] == "module"
        assert sections[1][0] == "unit"

    def test_no_sections(self):
        assert extract_sections("Просто текст без заголовков.\nЕщё текст.") == []


class TestChunking:
    def test_section_prefix_enrichment(self):
        from src.knowledge import _make_chunks

        text = "Параграф 12: Атмосфера\nВоздух состоит из азота и кислорода."
        chunks = _make_chunks(text, source="book.pdf", subject="география", grade="6")
        assert chunks[0].section_number == "12"
        assert "Параграф 12" in chunks[0].text
        assert chunks[0].subject == "география"
        assert chunks[0].grade == "6"

    def test_long_section_split(self):
        from src.knowledge import _make_chunks

        paragraph = "слово " * 400
        text = f"Параграф 1. Тема\n{paragraph}\n\n{paragraph}"
        chunks = _make_chunks(text, source="s", subject=None, grade=None)
        assert len(chunks) >= 2
        assert all(len(c.text) <= 1600 for c in chunks)

    def test_plain_text_chunks(self):
        from src.knowledge import _make_chunks

        text = (
            "Атмосфера — воздушная оболочка Земли, удерживаемая гравитацией.\n\n"
            "Кислород составляет 21% воздуха, а азот — почти 78%.\n\n"
            "Тропосфера — нижний слой атмосферы, где формируется погода."
        )
        chunks = _make_chunks(text, source="s", subject=None, grade=None)
        assert len(chunks) == 1  # короткий текст — один чанк
        assert "Атмосфера — воздушная оболочка" in chunks[0].text

    def test_short_paragraphs_dropped(self):
        from src.knowledge import _make_chunks

        # Параграфы короче 30 символов (обрывки навигации) не попадают в чанки
        text = (
            "Войти\n\n"
            "Далее\n\n"
            "Атмосфера — воздушная оболочка Земли, удерживаемая гравитацией.\n\n"
            "Кислород составляет 21% воздуха, а азот — почти 78%."
        )
        chunks = _make_chunks(text, source="s", subject=None, grade=None)
        merged = "\n".join(c.text for c in chunks)
        assert "Войти" not in merged
        assert "Атмосфера — воздушная оболочка" in merged
        assert "Кислород составляет 21%" in merged

    def test_web_noise_filtered_from_chunks(self):
        from src.knowledge import _make_chunks

        text = (
            "Атмосфера — воздушная оболочка Земли.\n\n"
            "-->\n\n"
            "Войти\n\n"
            "Зарегистрироваться / Создать сайт\n\n"
            "Скидки до 50% на комплекты\n\n"
            "Кислород составляет 21% воздуха."
        )
        chunks = _make_chunks(text, source="https://site.ru/page", subject="география", grade="6")
        merged = "\n".join(c.text for c in chunks)
        assert "Атмосфера — воздушная оболочка" in merged
        assert "Кислород составляет 21%" in merged
        # мусор не попал в чанки
        assert "-->" not in merged
        assert "Войти" not in merged
        assert "Зарегистрироваться" not in merged
        assert "Скидки" not in merged

    def test_web_noise_exact_words(self):
        from src.knowledge import _is_web_noise

        assert _is_web_noise("-->")
        assert _is_web_noise("Войти")
        assert _is_web_noise("Все блоги")
        assert _is_web_noise("Зарегистрироваться")
        assert _is_web_noise("x")
        assert _is_web_noise("")
        assert not _is_web_noise("Атмосфера — воздушная оболочка Земли")
        assert not _is_web_noise("Кислород составляет 21%")

    def test_slide_chrome_detected(self):
        from src.knowledge import _is_slide_chrome, _is_web_noise

        assert _is_slide_chrome("Часть 5")
        assert _is_slide_chrome("Слайд 12")
        assert _is_slide_chrome("Вернуться в меню")
        assert _is_slide_chrome("Поэты Серебряного Века - презентация онлайн")
        assert _is_slide_chrome("Категория: Литература")
        assert _is_slide_chrome("ВЫПОЛНИЛА: РЯЗАНЦЕВА С. М.")
        assert _is_slide_chrome("565.99K")
        assert not _is_slide_chrome("Серебряный век — период в русской культуре")

        assert _is_web_noise("Часть 3")
        assert _is_web_noise("Вернуться в меню")
        assert _is_web_noise("Категория: Литература")
        assert _is_web_noise("565.99K")
        assert not _is_web_noise("Серебряный век — период в русской культуре")

    def test_slideshow_text_rejected_from_chunks(self):
        from src.knowledge import _is_slideshow_text, _make_chunks

        slides = (
            "Поэты Серебряного Века - презентация онлайн\n"
            "565.99K\n"
            "Категория: Литература\n"
            "Часть 1\n"
            "Вернуться в меню\n"
            "ВЫПОЛНИЛА: РЯЗАНЦЕВА С. М.\n"
            "Часть 2\n"
            "Символизм\n"
            "Часть 3\n"
            "Похожие презентации:\n"
        )
        assert _is_slideshow_text(slides)
        assert _make_chunks(slides, source="https://ppt-online.org/x", subject="литература", grade="11") == []

    def test_slideshow_detects_many_short_lines(self):
        from src.knowledge import _is_slideshow_text

        text = "\n".join(
            ["Символизм", "Акмеизм", "Футуризм", "Имажинизм",
             "Серебряный век", "Русская поэзия", "Введение", "Заключение",
             "Источники", "Содержание", "Основные черты", "Темы"]
        )
        # много коротких строк и почти нет длинных предложений — слайд-шоу
        assert _is_slideshow_text(text)

    def test_real_paragraph_not_slideshow(self):
        from src.knowledge import _is_slideshow_text

        prose = (
            "Серебряный век — период в истории русской культуры с 1890-х по начало 1920-х годов. "
            "Название является поэтическим и отражает расцвет модернизма в литературе и искусстве.\n"
            "Поэты серебряного века стремились обновить литературный язык и отойти от реализма XIX века."
        )
        assert not _is_slideshow_text(prose)


class TestVectorStore:
    def test_add_search_and_filter(self):
        store = NumpyVectorStore("t", FakeEmbedder())
        store.add([
            FakeChunk("c1", "Атмосфера состоит из азота и кислорода.", "12", "география").to_doc("book.pdf", "география", "6"),
            FakeChunk("c2", "Литосфера — твёрдая оболочка Земли.", "5", "география").to_doc("book.pdf", "география", "6"),
        ])
        assert store.count() == 2

        res = store.search("азот кислород атмосфера", k=1)
        assert res and res[0].chunk.id == "c1"

        res = store.search("атмосфера", k=2, filters={"subject": "география"})
        assert len(res) == 2

        res = store.search("атмосфера", k=2, filters={"section_number": "5"})
        assert len(res) == 1
        assert res[0].chunk.section_number == "5"

    def test_search_empty_store(self):
        store = NumpyVectorStore("t", FakeEmbedder())
        assert store.search("anything") == []

    def test_reset(self):
        store = NumpyVectorStore("t", FakeEmbedder())
        store.add([FakeChunk("c1", "текст", "1", None).to_doc("s", None, None)])
        store.reset()
        assert store.count() == 0

    def test_chroma_backend_unavailable_hint(self, monkeypatch):
        import builtins

        original = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "chromadb":
                raise ImportError("no MSVC")
            return original(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(RuntimeError) as exc:
            make_store("x", FakeEmbedder(), backend="chroma")
        assert "Redistributable" in str(exc.value) or "numpy" in str(exc.value)

    def test_make_store_default_numpy(self, make_settings):
        s = make_settings(VECTOR_STORE="numpy")
        store = make_store("x", FakeEmbedder(), settings=s)
        assert isinstance(store, NumpyVectorStore)


@pytest.fixture
def make_settings(monkeypatch):
    def _make(**kwargs):
        for name in Settings.model_fields:
            if name not in kwargs:
                monkeypatch.delenv(name, raising=False)
        return Settings(_env_file=None, **kwargs)

    return _make


class FakeChunk:
    def __init__(self, cid, text, section, subject):
        self.cid, self.text, self.section, self.subject = cid, text, section, subject

    def to_doc(self, source, subject, grade):
        from src.knowledge import DocChunk

        return DocChunk(
            id=self.cid, text=self.text, section_number=self.section,
            source=source, subject=self.subject, grade=grade,
        )


class TestApiEmbedder:
    def test_batching_and_prefix(self, monkeypatch):
        calls = {"payloads": []}

        class FakeResp:
            def __init__(self, n):
                self.n = n

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"embedding": [float(i)]} for i in range(self.n)]}

        def fake_post(url, json, headers, timeout):
            calls["payloads"].append(json)
            n = len(json["input"])
            return FakeResp(n)

        monkeypatch.setattr("src.knowledge.httpx.post", fake_post)
        emb = ApiEmbedder(base_url="https://x/v1", api_key="k", model="intfloat/multilingual-e5-large", batch_size=2)
        vecs = emb.encode(["a", "b", "c"])
        assert len(vecs) == 3
        assert len(calls["payloads"]) == 2  # batch_size=2 → 2 запроса
        # e5-семейство: документы с префиксом passage:
        assert calls["payloads"][0]["input"][0].startswith("passage: ")

    def test_query_prefix(self, monkeypatch):
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"embedding": [1.0]}]}

        def fake_post(url, json, headers, timeout):
            captured["input"] = json["input"]
            return FakeResp()

        monkeypatch.setattr("src.knowledge.httpx.post", fake_post)
        emb = ApiEmbedder(base_url="https://x/v1", api_key="k", model="intfloat/multilingual-e5-large")
        emb.encode_query("атмосфера")
        assert captured["input"][0].startswith("query: ")

    def test_retries_on_temporary_503(self, monkeypatch):
        """503 → ретрай с бэк-оффом → успех; итоговая ошибка 5xx бросается."""
        calls = {"n": 0}

        class FakeResp:
            def __init__(self, status):
                self.status_code = status

            def raise_for_status(self):
                if self.status_code >= 400:
                    import httpx
                    raise httpx.HTTPStatusError("err", request=None, response=None)

            def json(self):
                return {"data": [{"embedding": [1.0]}]}

        def fake_post(url, json, headers, timeout):
            calls["n"] += 1
            return FakeResp(503 if calls["n"] < 3 else 200)

        monkeypatch.setattr("src.knowledge.httpx.post", fake_post)
        emb = ApiEmbedder(
            base_url="https://x/v1", api_key="k", model="intfloat/multilingual-e5-large",
            retries=3, retry_backoff=0.0,
        )
        vecs = emb.encode(["а"])
        assert len(vecs) == 1
        assert calls["n"] == 3  # 2 сбоя + успех

    def test_raises_after_retries_exhausted(self, monkeypatch):
        import httpx

        class FakeResp:
            status_code = 503

            def raise_for_status(self):
                raise httpx.HTTPStatusError("err", request=None, response=None)

            def json(self):
                return {"data": []}

        def fake_post(url, json, headers, timeout):
            return FakeResp()

        monkeypatch.setattr("src.knowledge.httpx.post", fake_post)
        emb = ApiEmbedder(
            base_url="https://x/v1", api_key="k", model="intfloat/multilingual-e5-large",
            retries=2, retry_backoff=0.0,
        )
        with pytest.raises(httpx.HTTPStatusError):
            emb.encode(["а"])


class TestMakeEmbedder:
    @pytest.fixture
    def make_settings(self, monkeypatch):
        def _make(**kwargs):
            for name in Settings.model_fields:
                if name not in kwargs:
                    monkeypatch.delenv(name, raising=False)
            return Settings(_env_file=None, **kwargs)

        return _make

    def test_default_api(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k", EMBEDDING_PROVIDER="api")
        emb = make_embedder(s)
        assert isinstance(emb, ApiEmbedder)
        assert emb.model == "intfloat/multilingual-e5-large"

    def test_api_without_key_raises(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="", EMBEDDING_PROVIDER="api")
        with pytest.raises(RuntimeError):
            make_embedder(s)

    def test_local_without_torch_raises_hint(self, make_settings, monkeypatch):
        s = make_settings(ROUTERAI_API_KEY="k", EMBEDDING_PROVIDER="local")

        def fake_import(name, *a, **kw):
            raise ImportError("no MSVC")

        monkeypatch.setattr("src.knowledge.sentence_transformers", None, raising=False)
        import builtins

        original = builtins.__import__
        monkeypatch.setattr(
            builtins, "__import__",
            lambda name, *a, **k: (_ for _ in ()).throw(ImportError("no torch")) if name == "sentence_transformers" else original(name, *a, **k),
        )
        with pytest.raises(RuntimeError) as exc:
            make_embedder(s).encode(["x"])
        assert "MSVC" in str(exc.value) or "Redistributable" in str(exc.value)

    def test_unknown_provider_raises(self, make_settings):
        s = make_settings(EMBEDDING_PROVIDER="bogus")
        with pytest.raises(ValueError):
            make_embedder(s)


class TestProcessDocument:
    def test_txt_pipeline(self, tmp_path: Path):
        src = tmp_path / "doc.txt"
        src.write_text(
            "Параграф 12: Атмосфера\nСтроение атмосферы. Атмосфера состоит из нескольких "
            "слоёв, каждый из которых выполняет свою роль в защите планеты.\n\n"
            "Параграф 13: Погода\nПогода меняется каждый день. Её определяют температура, "
            "влажность и движение воздушных масс.",
            encoding="utf-8",
        )
        store = NumpyVectorStore("docs", FakeEmbedder())
        stats = process_document(src, source="doc.txt", store=store, subject="география", grade="6")
        assert stats["num_chunks"] == 2
        assert store.count() == 2
        res = store.search("погода", k=1)
        assert "13" in res[0].chunk.section_number

    def test_pdf_fallback_pdfplumber(self, tmp_path: Path):
        pdf = _minimal_pdf(tmp_path / "m.pdf")
        text = parse_pdf(pdf)
        assert text.strip()

    def test_unsupported_format(self, tmp_path: Path):
        f = tmp_path / "x.xyz"
        f.write_text("data", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_document(f)


def _minimal_pdf(path: Path) -> Path:
    """Минимальный валидный PDF с текстом 'Hello PDF'."""
    body = (
        b"BT\n/F1 24 Tf\n72 720 Td\n(Hello PDF) Tj\nET\n"
    )
    content = f"<</Length {len(body)}>>\nstream\n".encode() + body + b"endstream\n"
    pdf = (
        b"%PDF-1.1\n"
        b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
        b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
        b"3 0 obj <</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources <</Font <</F1 5 0 R>>>>>> endobj\n"
        b"4 0 obj " + content + b"endobj\n"
        b"5 0 obj <</Type /Font /Subtype /Type1 /BaseFont /Helvetica>> endobj\n"
        b"trailer <</Root 1 0 R /Size 5>>\n"
        b"%%EOF\n"
    )
    path.write_bytes(pdf)
    return path


class TestRealEmbeddings:
    """Интеграционный тест: реальные эмбеддинги RouterAI (если ключ в .env)."""

    @pytest.mark.skipif(
        not (BASE_DIR / ".env").exists() or not Settings().ROUTERAI_API_KEY,
        reason="Нет ROUTERAI_API_KEY",
    )
    def test_real_api_embedding_dim(self):
        emb = make_embedder()
        vecs = emb.encode(["Атмосфера — воздушная оболочка Земли."])
        assert len(vecs) == 1
        assert len(vecs[0]) > 0


class TestOcr:
    def test_detect_text_layer(self):
        assert detect_text_layer("") is True
        assert detect_text_layer("маленький текст", min_chars=100) is True
        assert detect_text_layer("достаточно длинный текст для порога" * 5, min_chars=100) is False

    def test_validate_topic_in_text(self):
        assert validate_topic_in_text("Атмосфера", "тут много текста про атмосферу и воздух") is True
        assert validate_topic_in_text("Атмосфера", "тут текст про литосферу") is False
        assert validate_topic_in_text("", "любой текст") is True  # нет темы — пропускаем

    def test_extract_printed_number_bottom_band(self):
        items = [
            ([[0, 10], [200, 10], [200, 40], [0, 40]], "заголовок", 0.9),
            ([[0, 700], [60, 700], [60, 740], [0, 740]], "23", 0.9),
        ]
        assert _extract_printed_number(items) == 23

    def test_extract_printed_number_no_bottom(self):
        assert _extract_printed_number([([[0, 10], [200, 10], [200, 40], [0, 40]], "заголовок", 0.9)]) is None

    def test_consistent_offset(self):
        assert _consistent_offset({2: 13, 3: 14}) == 11   # 13-2=11, 14-3=11
        assert _consistent_offset({2: 13, 3: 10}) is None  # несогласованно
        assert _consistent_offset({2: None, 3: 14}) is None

    def test_ocr_pages_mocked(self, monkeypatch):
        import sys
        import types

        class FakeReader:
            def __init__(self):
                self.page = 0

            def readtext(self, image, **kw):
                self.page += 1
                num = "13" if self.page == 1 else "14"
                return [
                    ([[0, 10], [200, 10], [200, 40], [0, 40]], "текст страницы", 0.9),
                    ([[0, 700], [60, 700], [60, 740], [0, 740]], num, 0.9),
                ]

        class FakePage:
            def render(self, scale=1.0):
                return self

            def to_pil(self):
                return None

        class FakeDoc:
            def __init__(self, n):
                self.n = n

            def __len__(self):
                return self.n

            def __getitem__(self, i):
                return FakePage()

        monkeypatch.setattr("src.knowledge._get_ocr_reader", lambda langs: FakeReader())
        fake = types.ModuleType("pypdfium2")
        fake.PdfDocument = lambda path: FakeDoc(100)
        monkeypatch.setitem(sys.modules, "pypdfium2", fake)

        res = ocr_pages(Path("x.pdf"), (2, 3), langs=("ru",), detect_numbers=True)
        assert "текст страницы" in res["text"]
        assert res["page_numbers"] == {2: 13, 3: 14}
        assert res["offset"] == 11


class TestHybridRag:
    """Гибридный retrieval: BM25 (Okapi) + векторный поиск + fusion RRF (7.2)."""

    def _store(self, chunks):
        store = HybridVectorStore(NumpyVectorStore("hyb", FakeEmbedder()))
        store.add(chunks)
        return store

    def _chunk(self, cid, text, section="12"):
        return DocChunk(
            id=cid, text=text, section_number=section,
            section_title="Тема", source="book", subject="география", grade="6",
        )

    def test_bm25_scores_exact_terms(self):
        bm25 = OkapiBM25()
        bm25.add_docs([
            "Атмосфера — воздушная оболочка Земли.",
            "Гидросфера — водная оболочка Земли.",
        ])
        assert bm25.score("атмосфера", 0) > bm25.score("атмосфера", 1)
        assert bm25.score("водная", 1) > bm25.score("водная", 0)

    def test_rrf_merge_combines_rankings(self):
        s1 = [SearchResult(chunk=self._chunk("a", "А"), score=0.1),
              SearchResult(chunk=self._chunk("b", "Б"), score=0.2)]
        s2 = [SearchResult(chunk=self._chunk("b", "Б"), score=0.05)]
        merged = rrf_merge([s1, s2], k=2)
        assert [r.chunk.id for r in merged] == ["b", "a"]

    def test_hybrid_returns_top_chunks(self):
        store = self._store([
            self._chunk("c1", "Атмосфера состоит из азота и кислорода."),
            self._chunk("c2", "Реки текут по равнинам и питают гидросферу."),
            self._chunk("c3", "Давление воздуха называется атмосферным давлением."),
        ])
        res = store.search("атмосфера давление", k=2)
        ids = [r.chunk.id for r in res]
        # векторный ретривер находит c1 (точное «атмосфера»), BM25 — c3 (точное «давление»),
        # RRF объединяет оба сигнала
        assert {"c1", "c3"} <= set(ids)
        assert len(res) <= 2

    def test_hybrid_respects_filters(self):
        chunks = [
            self._chunk("c1", "Атмосфера — воздушная оболочка.", "12"),
            self._chunk("c2", "Атмосферное давление изменяется с высотой.", "13"),
        ]
        store = self._store(chunks)
        res = store.search("атмосфера", k=5, filters={"section_number": "13"})
        assert [r.chunk.id for r in res] == ["c2"]
        assert store.count() == 2

    def test_hybrid_reset(self):
        store = self._store([self._chunk("c1", "Атмосфера — воздушная оболочка.")])
        store.reset()
        assert store.count() == 0
        assert store.search("атмосфера") == []


class TestOcrIntegration:
    """Реальный EasyOCR (тяжёлый: модель ~64MB). Включается флагом EDUTUTOR_RUN_OCR=1."""

    @pytest.mark.skipif(
        os.getenv("EDUTUTOR_RUN_OCR", "0") != "1",
        reason="OCR-интеграция: EDUTUTOR_RUN_OCR=1",
    )
    def test_ocr_pages_real(self, tmp_path: Path):
        pdf = _minimal_pdf(tmp_path / "m.pdf")
        res = ocr_pages(pdf, (1, 1), langs=("en",), detect_numbers=False)
        assert res["text"].strip(), "OCR не извлёк текст"
        assert "Hello" in res["text"]
