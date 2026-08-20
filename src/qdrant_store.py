"""
EduTutor — Qdrant векторное хранилище (roadmap #1).

Реализует интерфейс VectorStore (add/search/count/reset) поверх Qdrant:
  - server mode: URL (docker-compose, docker-compose.yml, порт 6333);
  - embedded mode: path (локальная персистентная БД без внешнего сервера —
    удобно для разработки/тестов и машин без Docker).

Payload-поля: subject, grade, section_number, section_title, source, page_number —
для filtered search (roadmap: payload fields). Коллекция создаётся при старте,
если её нет (миграция). Имя коллекции включает размерность эмбеддинга
(edututor_1024 / edututor_384) — разные эмбеддинги не конфликтуют.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from .knowledge import DocChunk, SearchResult, VectorStore

logger = logging.getLogger("edututor.qdrant")

# Поля, которые хранятся в payload для metadata-фильтрации (roadmap #1)
_PAYLOAD_FIELDS = ("source", "subject", "grade", "section_number", "section_title", "page_number")


def _chunk_uuid(chunk_id: str) -> uuid.UUID:
    """Детерминированный UUID точки из chunk.id (Qdrant требует int/UUID id)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)


def _to_qdrant_filter(filters: Optional[Dict[str, Any]]):
    """Конвертация нашего filters-словаря в Qdrant Filter.

    Поддерживает два синтаксиса (как chunk_matches): прямое равенство
    ({"section_number": "12"}) и форму {"$eq": ...}.
    """
    if not filters:
        return None
    from qdrant_client import models

    must = []
    for key, value in filters.items():
        if isinstance(value, dict):
            value = value.get("$eq")
        if value is None:
            continue
        must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
    if not must:
        return None
    return models.Filter(must=must)


class QdrantStore:
    """Обёртка над Qdrant: добавление, семантический поиск, фильтр по метаданным.

    Вектора нормализуются эмбеддером; метрика — COSINE. В payload точек хранится
    полный текст чанка + метаданные, поэтому search возвращает полноценные DocChunk.
    """

    def __init__(
        self,
        collection_name: str,
        embedder: Any,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        path: Optional[Any] = None,
        vector_size: Optional[int] = None,
        prefer_grpc: bool = False,
    ) -> None:
        from qdrant_client import QdrantClient

        self.collection_name = collection_name
        self.embedder = embedder
        self.vector_size = vector_size
        if path:
            # Embedded persistent mode: локальная БД в каталоге (без сервера)
            self._client = QdrantClient(path=str(path), prefer_grpc=prefer_grpc)
        else:
            self._client = QdrantClient(
                url=url or "http://localhost:6333",
                api_key=api_key or None,
                prefer_grpc=prefer_grpc,
            )
        self._ensure_collection()

    # --- миграция: при старте проверить коллекцию, создать если нет (roadmap #1) ---
    def _ensure_collection(self) -> None:
        from qdrant_client import models

        if self._client.collection_exists(self.collection_name):
            info = self._client.get_collection(self.collection_name)
            if self.vector_size is None:
                self.vector_size = int(info.config.params.vectors.size)
            return
        if self.vector_size is None:
            raise ValueError(
                "QdrantStore: vector_size не определён (передайте размерность эмбеддинга)"
            )
        self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=self.vector_size, distance=models.Distance.COSINE
            ),
        )
        logger.info("Qdrant collection создана: %s (dim=%s)", self.collection_name, self.vector_size)

    def _payload(self, chunk: DocChunk) -> Dict[str, Any]:
        payload = {"chunk_id": chunk.id, "text": chunk.text}
        for field in _PAYLOAD_FIELDS:
            value = getattr(chunk, field, None)
            if value is not None:
                payload[field] = value
        return payload

    @staticmethod
    def _chunk_from_payload(payload: Dict[str, Any]) -> DocChunk:
        return DocChunk(
            id=str(payload.get("chunk_id", "")),
            text=str(payload.get("text", "")),
            source=str(payload.get("source", "")),
            subject=payload.get("subject"),
            grade=payload.get("grade"),
            section_number=payload.get("section_number"),
            section_title=payload.get("section_title"),
            page_number=payload.get("page_number"),
        )

    def add(self, chunks: List[DocChunk]) -> None:
        if not chunks:
            return
        from qdrant_client import models

        vectors = self.embedder.encode([c.text for c in chunks])
        points = [
            models.PointStruct(
                id=_chunk_uuid(c.id),
                vector=vector,
                payload=self._payload(c),
            )
            for c, vector in zip(chunks, vectors)
        ]
        self._client.upsert(self.collection_name, points=points)

    def search(
        self,
        query: str,
        k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        if self.count() == 0:
            return []
        vec = self.embedder.encode_query(query)
        response = self._client.query_points(
            self.collection_name,
            query=vec,
            query_filter=_to_qdrant_filter(filters),
            limit=k,
            with_payload=True,
        )
        results = []
        for point in response.points:
            chunk = self._chunk_from_payload(point.payload or {})
            # Qdrant возвращает cosine-сходство (ближе = больше).
            # Для единообразия с NumpyStore/ChromaStore: score = 1 - similarity.
            results.append(SearchResult(chunk=chunk, score=float(1.0 - point.score)))
        return results

    def count(self) -> int:
        try:
            return self._client.count(self.collection_name).count
        except Exception:
            return 0

    def reset(self) -> None:
        """Полная очистка: удаляем все точки (работает и в embedded-режиме).

        Удаление коллекции + пересоздание в embedded-режиме (QdrantClient(path=...))
        оставляет stale-данные — поэтому чистим через delete с пустым фильтром.
        """
        from qdrant_client import models

        self._client.delete(
            self.collection_name,
            points_selector=models.FilterSelector(filter=models.Filter(must=[])),
        )

    def delete(self, ids: List[str]) -> None:
        """Удаление точек по исходным chunk.id (roadmap: search, add, delete)."""
        from qdrant_client import models

        if not ids:
            return
        self._client.delete(
            self.collection_name,
            points_selector=models.PointIdsList(points=[_chunk_uuid(i) for i in ids]),
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
