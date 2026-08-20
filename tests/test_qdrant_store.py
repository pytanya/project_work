"""Тесты Qdrant векторного хранилища (roadmap #1).

QdrantStore тестируется в embedded-режиме (локальный персистентный каталог) —
внешний Qdrant-сервер (docker-compose.yml) для этого не нужен.
"""

from __future__ import annotations

import hashlib

import pytest

from src.knowledge import DocChunk, make_qdrant_store, make_store
from src.qdrant_store import QdrantStore, _chunk_uuid


class FakeEmbedder:
    """Детерминированный эмбеддер (как в test_knowledge.py): 8-dim по хэшам токенов."""

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


def _chunk(cid, text, section="12", subject="география", grade="6", source="book.pdf"):
    return DocChunk(
        id=cid, text=text, section_number=section, section_title="Тема",
        source=source, subject=subject, grade=grade,
    )


class TestQdrantStore:
    def test_add_search_and_filter(self, tmp_path):
        store = QdrantStore("t", FakeEmbedder(), path=str(tmp_path), vector_size=8)
        store.add([
            _chunk("c1", "Атмосфера состоит из азота и кислорода.", "12"),
            _chunk("c2", "Литосфера — твёрдая оболочка Земли.", "5"),
        ])
        assert store.count() == 2

        res = store.search("азот кислород атмосфера", k=1)
        assert res and res[0].chunk.id == "c1"

        res = store.search("атмосфера", k=2, filters={"subject": "география"})
        assert len(res) == 2

        res = store.search("атмосфера", k=2, filters={"section_number": "5"})
        assert len(res) == 1
        assert res[0].chunk.section_number == "5"
        store.close()

    def test_search_empty_store(self, tmp_path):
        store = QdrantStore("t", FakeEmbedder(), path=str(tmp_path), vector_size=8)
        assert store.search("anything") == []
        store.close()

    def test_reset_empties_store(self, tmp_path):
        store = QdrantStore("t", FakeEmbedder(), path=str(tmp_path), vector_size=8)
        store.add([_chunk("c1", "текст", "1")])
        assert store.count() == 1
        store.reset()
        assert store.count() == 0
        assert store.search("текст") == []
        store.close()

    def test_persistence_across_instances(self, tmp_path):
        """Embedded-режим персистентен: данные переживают пересоздание стора."""
        store = QdrantStore("t", FakeEmbedder(), path=str(tmp_path), vector_size=8)
        store.add([_chunk("c1", "Атмосфера — воздушная оболочка Земли.", "12")])
        store.close()

        store2 = QdrantStore("t", FakeEmbedder(), path=str(tmp_path), vector_size=8)
        assert store2.count() == 1
        res = store2.search("атмосфера оболочка", k=1)
        assert res and res[0].chunk.id == "c1"
        # payload-поля восстанавливаются полностью
        assert res[0].chunk.section_number == "12"
        assert res[0].chunk.subject == "география"
        store2.close()

    def test_delete_by_ids(self, tmp_path):
        store = QdrantStore("t", FakeEmbedder(), path=str(tmp_path), vector_size=8)
        store.add([
            _chunk("c1", "Атмосфера — воздушная оболочка.", "12"),
            _chunk("c2", "Литосфера — твёрдая оболочка.", "5"),
        ])
        store.delete(["c1"])
        assert store.count() == 1
        res = store.search("атмосфера", k=5)
        assert [r.chunk.id for r in res] == ["c2"]
        store.close()

    def test_chunk_uuid_deterministic(self):
        assert _chunk_uuid("c1") == _chunk_uuid("c1")
        assert _chunk_uuid("c1") != _chunk_uuid("c2")


class TestMakeQdrantStore:
    def test_embedded_path_from_settings(self, monkeypatch, tmp_path):
        from src.config import Settings

        for name in Settings.model_fields:
            monkeypatch.delenv(name, raising=False)
        s = Settings(_env_file=None, VECTOR_STORE="qdrant", QDRANT_PATH=str(tmp_path))
        store = make_store("x", FakeEmbedder(), settings=s)
        assert isinstance(store, QdrantStore)
        assert store.count() == 0
        store.close()

    def test_make_qdrant_store_with_known_dim(self, monkeypatch, tmp_path):
        from src.config import Settings

        for name in Settings.model_fields:
            monkeypatch.delenv(name, raising=False)
        s = Settings(
            _env_file=None, VECTOR_STORE="qdrant",
            QDRANT_PATH=str(tmp_path), EMBEDDING_API_MODEL="intfloat/multilingual-e5-large",
        )
        emb = FakeEmbedder(model="intfloat/multilingual-e5-large")
        store = make_qdrant_store("x", emb, settings=s)
        # размерность из статической карты (1024) — коллекция создалась без encode-вызова
        assert store.vector_size == 1024
        store.close()

    def test_unknown_backend_raises(self, monkeypatch):
        from src.config import Settings

        for name in Settings.model_fields:
            monkeypatch.delenv(name, raising=False)
        s = Settings(_env_file=None, VECTOR_STORE="bogus")
        with pytest.raises(ValueError):
            make_store("x", FakeEmbedder(), settings=s)
