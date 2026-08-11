"""
EduTutor — RAG: обработка документов, чанкинг, ChromaDB, embeddings, реранкинг.

- Embedder: два провайдера — ApiEmbedder (RouterAI /embeddings, без локального
  torch/VC++) и LocalEmbedder (sentence-transformers, после установки MSVC).
- ChromaStore: персистентный/эпизодический ChromaDB, cosine, метаданные
  (subject, grade, section_number) + фильтр `where` (раздел 3.3).
- parse_document: Docling → pdfplumber (fallback) → plain text.
- chunk_text: разделение по параграфам/главам с сохранением иерархии (13.2).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import httpx

from pydantic import BaseModel, Field

from .config import settings as default_settings

logger = logging.getLogger("edututor.knowledge")

MAX_CHUNK_CHARS = 1500
API_BATCH = 16

_E5_FAMILY = ("e5", "bge")


class DocChunk(BaseModel):
    """Чанк документа с метаданными для RAG."""

    id: str
    text: str
    section_number: Optional[str] = None
    section_title: Optional[str] = None
    source: str = ""
    subject: Optional[str] = None
    grade: Optional[str] = None

    def metadata(self) -> Dict[str, Any]:
        meta = {"source": self.source}
        if self.section_number:
            meta["section_number"] = self.section_number
        if self.section_title:
            meta["section_title"] = self.section_title
        if self.subject:
            meta["subject"] = self.subject
        if self.grade:
            meta["grade"] = self.grade
        return meta


@dataclass
class SearchResult:
    """Результат RAG-поиска."""

    chunk: DocChunk
    score: float  # расстояние (cosine: меньше — ближе)


# ----------------------------------------------------------------------
# Embeddings
# ----------------------------------------------------------------------
class Embedder(Protocol):
    """Протокол эмбеддера."""

    def encode(self, texts: List[str]) -> List[List[float]]: ...

    def encode_query(self, text: str) -> List[float]: ...


def _e5_prefix(model: str, kind: str) -> str:
    """Префиксы query:/passage: для моделей семейства e5/bge (рекомендация HuggingFace)."""
    ml = model.lower()
    if any(f in ml for f in _E5_FAMILY):
        return "query: " if kind == "query" else "passage: "
    return ""


class ApiEmbedder:
    """Эмбеддинги через OpenAI-совместимый endpoint провайдера (RouterAI /embeddings).

    Не требует локального torch/VC++; работает через API-ключ.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "intfloat/multilingual-e5-large",
        timeout: float = 60.0,
        batch_size: int = API_BATCH,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size

    def _call(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            payload = {"model": self.model, "input": batch}
            resp = httpx.post(
                self.base_url + "/embeddings",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            out.extend(d["embedding"] for d in data["data"])
        return out

    def encode(self, texts: List[str]) -> List[List[float]]:
        return self._call([_e5_prefix(self.model, "passage") + t for t in texts])

    def encode_query(self, text: str) -> List[float]:
        return self._call([_e5_prefix(self.model, "query") + text])[0]


class LocalEmbedder:
    """Эмбеддинги через sentence-transformers (intfloat/multilingual-e5-small).

    Требует установленного MSVC Redistributable (torch DLL). Ленивая загрузка модели.
    """

    def __init__(self, model: str = "intfloat/multilingual-e5-small") -> None:
        self.model = model
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as e:  # OSError/ImportError (нет MSVC → torch не грузится)
                raise RuntimeError(
                    "Не удалось загрузить sentence-transformers. "
                    "Установите Microsoft Visual C++ Redistributable "
                    "(https://aka.ms/vs/17/release/vc_redist.x64.exe) или "
                    "используйте EMBEDDING_PROVIDER=api."
                ) from e
            self._model = SentenceTransformer(self.model)
        return self._model

    def encode(self, texts: List[str]) -> List[List[float]]:
        model = self._load()
        prefixed = [_e5_prefix(self.model, "passage") + t for t in texts]
        vecs = model.encode(prefixed, normalize_embeddings=True)
        return [v.tolist() for v in vecs]

    def encode_query(self, text: str) -> List[float]:
        model = self._load()
        vec = model.encode(
            [_e5_prefix(self.model, "query") + text], normalize_embeddings=True
        )[0]
        return vec.tolist()


def make_embedder(settings: Any = None) -> Embedder:
    """Фабрика эмбеддера по EMBEDDING_PROVIDER (api|local)."""
    s = settings or default_settings
    provider = (s.EMBEDDING_PROVIDER or "api").strip().lower()
    if provider == "local":
        return LocalEmbedder(model=s.EMBEDDING_MODEL)
    if provider == "api":
        if not s.ROUTERAI_API_KEY:
            raise RuntimeError("EMBEDDING_PROVIDER=api требует ROUTERAI_API_KEY в .env")
        return ApiEmbedder(
            base_url=s.ROUTERAI_BASE_URL,
            api_key=s.ROUTERAI_API_KEY,
            model=s.EMBEDDING_API_MODEL or "intfloat/multilingual-e5-large",
        )
    raise ValueError(f"Неизвестный EMBEDDING_PROVIDER: {provider!r} (api|local)")


# ----------------------------------------------------------------------
# Чанкинг
# ----------------------------------------------------------------------
_SECTION_RE = re.compile(r"^(?:параграф|§|глава|раздел)\s*(\d{1,3})[.\s:-]*([^\n]*)", re.IGNORECASE)


def clean_pdf_text(text: str) -> str:
    """Очистка текста PDF (из geo_tutor pdf_processor): переносы, колонтитулы, пробелы."""
    if not text:
        return ""
    # дефисные переносы слов (конец строки)
    text = re.sub(r"-\s*\n", "", text)
    # мягкие переносы
    text = re.sub(r"\u00ad", "", text)
    # объединяем строки абзацев (одиночные переводы строк)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # множественные пробелы → один
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def extract_sections(text: str) -> List[tuple[str, str, str]]:
    """Детекция параграфов: (номер, заголовок, контент).

    Разбивает текст по заголовкам «Параграф N. Название» / «§N. Название».
    Если заголовков нет — возвращает пустой список.
    """
    lines = text.split("\n")
    sections: List[tuple[str, str, List[str]]] = []
    current: Optional[tuple[str, str, List[str]]] = None
    for line in lines:
        stripped = line.strip()
        m = _SECTION_RE.match(stripped)
        if m and len(stripped) < 200:
            if current:
                sections.append(current)
            current = (m.group(1), m.group(2).strip(), [])
            continue
        if current is not None:
            current[2].append(line)
    if current:
        sections.append(current)
    return [(num, title or "", "\n".join(content).strip()) for num, title, content in sections]


def _split_long(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """Разбиение длинного абзаца на окна по границам предложений (≤ max_chars)."""
    if len(text) <= max_chars:
        return [text]
    parts: List[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    buffer = ""
    for s in sentences:
        if buffer and len(buffer) + len(s) + 1 > max_chars:
            parts.append(buffer.strip())
            buffer = ""
        if len(s) > max_chars:  # предложение длиннее окна — режем посимвольно
            for i in range(0, len(s), max_chars):
                parts.append(s[i : i + max_chars])
            buffer = ""
            continue
        buffer += ("" if not buffer else " ") + s
    if buffer.strip():
        parts.append(buffer.strip())
    return parts


def _make_chunks(text: str, source: str, subject: Optional[str], grade: Optional[str]) -> List[DocChunk]:
    """Нарезка текста на чанки с обогащением «Параграф N: название» (13.2)."""
    chunks: List[DocChunk] = []
    idx = 0
    sections = extract_sections(text)
    if not sections:
        # нет структуры — режем по абзацам
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        buffer = ""
        for p in paragraphs:
            if buffer and len(buffer) + len(p) > MAX_CHUNK_CHARS:
                for part in _split_long(buffer):
                    chunks.append(_chunk(idx, part, source, subject, grade))
                    idx += 1
                buffer = ""
            buffer += p + "\n\n"
        for part in _split_long(buffer):
            chunks.append(_chunk(idx, part, source, subject, grade))
            idx += 1
        return chunks

    for num, title, content in sections:
        prefix = f"Параграф {num}" + (f": {title}" if title else "")
        if not content:
            chunks.append(_chunk(idx, prefix + "\n(пустой параграф)", source, subject, grade, num, title))
            idx += 1
            continue
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
        buffer = ""
        for p in paragraphs:
            if buffer and len(buffer) + len(p) > MAX_CHUNK_CHARS:
                for part in _split_long(buffer):
                    chunks.append(_chunk(idx, f"{prefix}\n{part}", source, subject, grade, num, title))
                    idx += 1
                buffer = ""
            buffer += p + "\n\n"
        for part in _split_long(buffer):
            chunks.append(_chunk(idx, f"{prefix}\n{part}", source, subject, grade, num, title))
            idx += 1
    return chunks


def _chunk(
    idx: int,
    text: str,
    source: str,
    subject: Optional[str],
    grade: Optional[str],
    section_number: Optional[str] = None,
    section_title: Optional[str] = None,
) -> DocChunk:
    digest = hashlib.md5(f"{source}:{idx}:{text[:40]}".encode("utf-8")).hexdigest()[:12]
    return DocChunk(
        id=f"{source.replace('/', '_')}:{idx}:{digest}",
        text=text,
        section_number=section_number,
        section_title=section_title,
        source=source,
        subject=subject,
        grade=grade,
    )


# ----------------------------------------------------------------------
# Парсинг документов
# ----------------------------------------------------------------------
def parse_pdf(path: Path) -> str:
    """Извлечение текста из PDF: Docling → pdfplumber (fallback) → error."""
    path = Path(path)
    try:
        from docling.document_converter import DocumentConverter  # noqa: WPS433

        result = DocumentConverter().convert(path)
        md = getattr(result.document, "export_to_markdown", lambda: "")()
        return clean_pdf_text(md)
    except Exception as e:  # docling недоступен/падает (нет MSVC и т.п.)
        logger.warning("Docling недоступен (%s) — использую pdfplumber", e)
        try:
            import pdfplumber  # noqa: WPS433

            text_parts: List[str] = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    if t:
                        text_parts.append(t)
            return clean_pdf_text("\n".join(text_parts))
        except Exception as e2:
            raise RuntimeError(f"Не удалось разобрать PDF {path}: {e2}") from e2


def parse_document(path: Path, source: str = "") -> str:
    """Разбор документа (pdf/docx/txt) в очищенный текст."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return clean_pdf_text(path.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".docx":
        try:
            from docling.document_converter import DocumentConverter  # noqa: WPS433

            result = DocumentConverter().convert(path)
            return clean_pdf_text(getattr(result.document, "export_to_markdown", lambda: "")())
        except Exception as e:
            raise RuntimeError(f"Не удалось разобрать DOCX {path}: {e}") from e
    raise ValueError(f"Неподдерживаемый формат файла: {suffix}")


# ----------------------------------------------------------------------
# ChromaDB / векторное хранилище
# ----------------------------------------------------------------------
class VectorStore(Protocol):
    """Протокол векторного хранилища."""

    def add(self, chunks: List[DocChunk]) -> None: ...

    def search(
        self,
        query: str,
        k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]: ...

    def count(self) -> int: ...

    def reset(self) -> None: ...


class NumpyVectorStore:
    """Портативное векторное хранилище (numpy, cosine) — без MSVC/ChromaDB.

    Полноценный бэкенд для MVP: семантический поиск + фильтр по метаданным
    (subject/grade/section_number/source). Используется по умолчанию
    (VECTOR_STORE=numpy); ChromaDB — опциональный бэкенд (VECTOR_STORE=chroma)
    после установки MSVC Redistributable.
    """

    def __init__(self, collection_name: str, embedder: Embedder) -> None:
        import numpy as np  # noqa: WPS433

        self.collection_name = collection_name
        self.embedder = embedder
        self._np = np
        self._matrix = np.zeros((0, 0), dtype=np.float32)
        self._chunks: List[DocChunk] = []

    def add(self, chunks: List[DocChunk]) -> None:
        if not chunks:
            return
        np = self._np
        embeddings = np.asarray(self.embedder.encode([c.text for c in chunks]), dtype=np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = np.divide(embeddings, norms, out=np.zeros_like(embeddings), where=norms != 0)
        if self._matrix.shape[0] == 0:
            self._matrix = embeddings
        else:
            self._matrix = np.vstack([self._matrix, embeddings])
        self._chunks.extend(chunks)

    def search(
        self,
        query: str,
        k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        if not self._chunks:
            return []
        np = self._np
        qv = np.asarray(self.embedder.encode_query(query), dtype=np.float32)
        qnorm = np.linalg.norm(qv)
        if qnorm == 0:
            return []
        qv = qv / qnorm

        # Фильтр по метаданным
        idx = list(range(len(self._chunks)))
        if filters:
            idx = [i for i in idx if self._chunk_matches(self._chunks[i], filters)]

        if not idx:
            return []
        sub = self._matrix[idx]
        sims = sub @ qv  # cosine (матрица и запрос нормализованы)
        order = np.argsort(-sims)
        results = []
        for rank in order[:k]:
            i = idx[int(rank)]
            results.append(SearchResult(chunk=self._chunks[i], score=float(1.0 - sims[int(rank)])))
        return results

    @staticmethod
    def _chunk_matches(chunk: DocChunk, filters: Dict[str, Any]) -> bool:
        meta = chunk.metadata()
        for key, expected in filters.items():
            if isinstance(expected, dict):
                if meta.get(key) != expected.get("$eq"):
                    return False
            else:
                if meta.get(key) != expected:
                    return False
        return True

    def count(self) -> int:
        return len(self._chunks)

    def reset(self) -> None:
        self._matrix = self._np.zeros((0, 0), dtype=self._np.float32)
        self._chunks = []


class ChromaStore:
    """Обёртка над ChromaDB: добавление, семантический поиск, фильтр by метаданным.

    Требует установленного MSVC Redistributable (chromadb_rust_bindings DLL).
    Используется при VECTOR_STORE=chroma.
    """

    def __init__(
        self,
        collection_name: str,
        embedder: Embedder,
        persist_dir: Optional[Path] = None,
    ) -> None:
        try:
            import chromadb  # noqa: WPS433
        except Exception as e:
            raise RuntimeError(
                "Не удалось загрузить ChromaDB. Установите Microsoft Visual C++ "
                "Redistributable (https://aka.ms/vs/17/release/vc_redist.x64.exe) "
                "или используйте VECTOR_STORE=numpy."
            ) from e

        self.embedder = embedder
        if persist_dir:
            persist_dir = Path(persist_dir)
            persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(persist_dir))
        else:
            self._client = chromadb.EphemeralClient()
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, chunks: List[DocChunk]) -> None:
        if not chunks:
            return
        embeddings = self.embedder.encode([c.text for c in chunks])
        self._collection.add(
            ids=[c.id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[c.metadata() for c in chunks],
            embeddings=embeddings,
        )

    def search(
        self,
        query: str,
        k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        vec = self.embedder.encode_query(query)
        kwargs: Dict[str, Any] = {"query_embeddings": [vec], "n_results": k}
        if filters:
            kwargs["where"] = filters
        res = self._collection.query(
            include=["documents", "metadatas", "distances"], **kwargs
        )
        results: List[SearchResult] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc_id in enumerate(ids):
            meta = metas[i] or {}
            results.append(
                SearchResult(
                    chunk=DocChunk(
                        id=doc_id,
                        text=docs[i],
                        source=meta.get("source", ""),
                        section_number=meta.get("section_number"),
                        section_title=meta.get("section_title"),
                        subject=meta.get("subject"),
                        grade=meta.get("grade"),
                    ),
                    score=float(dists[i]),
                )
            )
        return results

    def count(self) -> int:
        return self._collection.count()

    def reset(self) -> None:
        self._client.delete_collection(self._collection.name)


def make_store(
    collection_name: str,
    embedder: Embedder,
    persist_dir: Optional[Path] = None,
    backend: Optional[str] = None,
    settings: Any = None,
) -> VectorStore:
    """Фабрика векторного хранилища (numpy — по умолчанию, chroma — опционально)."""
    s = settings or default_settings
    backend = backend or (s.VECTOR_STORE or "numpy").strip().lower()
    if backend == "chroma":
        return ChromaStore(collection_name, embedder, persist_dir=persist_dir)
    if backend == "numpy":
        return NumpyVectorStore(collection_name, embedder)
    raise ValueError(f"Неизвестный VECTOR_STORE: {backend!r} (numpy|chroma)")


def process_document(
    path: Path,
    source: str,
    store: VectorStore,
    subject: Optional[str] = None,
    grade: Optional[str] = None,
) -> Dict[str, Any]:
    """Полный пайплайн: разбор → очистка → чанки → индекс (раздел 7.1 process_document)."""
    text = parse_document(path, source=source)
    chunks = _make_chunks(text, source=source, subject=subject, grade=grade)
    store.add(chunks)
    return {
        "source": source,
        "path": str(path),
        "chars": len(text),
        "num_chunks": len(chunks),
        "collection": getattr(store, "collection_name", "numpy"),
    }
