"""Тесты RAG (Слайс 4): чанкинг, очистка, ChromaDB, embeddings, parse. """

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.config import BASE_DIR, Settings
from src.knowledge import (
    ApiEmbedder,
    NumpyVectorStore,
    clean_pdf_text,
    extract_sections,
    make_embedder,
    make_store,
    parse_document,
    parse_pdf,
    process_document,
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
        num, title, content = sections[0]
        assert num == "12"
        assert title == "Атмосфера"
        assert "Строение атмосферы" in content

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

        text = "Абзац первый.\n\nАбзац второй.\n\nАбзац третий."
        chunks = _make_chunks(text, source="s", subject=None, grade=None)
        assert len(chunks) == 1  # короткий текст — один чанк
        assert "Абзац первый" in chunks[0].text


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
            "Параграф 12: Атмосфера\nСтроение атмосферы.\n\nПараграф 13: Погода\nПогода меняется.",
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
