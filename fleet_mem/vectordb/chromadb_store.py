"""ChromaDB vector store implementation."""

from __future__ import annotations

import logging
from pathlib import Path

import chromadb

from fleet_mem.vectordb.base import VectorDatabase
from fleet_mem.vectordb.errors import DimMismatchError
from fleet_mem.vectordb.types import VectorDocument

logger = logging.getLogger(__name__)


class ChromaDBStore(VectorDatabase):
    """ChromaDB-backed vector store using pre-computed embeddings."""

    def __init__(self, path: Path):
        self._client = chromadb.PersistentClient(path=str(path))
        # Cache: collection-name -> stored dimension (or None for legacy
        # collections that have no "dimension" metadata key). Populated
        # lazily by _get_collection_dim and consulted by _validate_dim
        # to avoid a metadata lookup on every insert/search call.
        self._collection_dim_cache: dict[str, int | None] = {}

    def _get_collection_dim(self, collection: str) -> int | None:
        """Return the collection's stored dimension, or None if unavailable.

        ChromaDB persists per-collection metadata set at creation time
        (see ``create_collection``). Legacy collections created before the
        ``dimension`` key was added have no dim metadata; for those we
        return None and the caller skips dim-validation (backward compat).
        """
        if collection in self._collection_dim_cache:
            return self._collection_dim_cache[collection]
        col = self._client.get_collection(name=collection)
        meta = col.metadata or {}
        raw = meta.get("dimension")
        dim: int | None
        if raw is None:
            dim = None
        else:
            try:
                dim = int(raw)
            except (TypeError, ValueError):
                dim = None
        self._collection_dim_cache[collection] = dim
        return dim

    def _validate_dim(
        self,
        collection: str,
        vector_dim: int,
        *,
        model_name: str = "<unknown>",
    ) -> None:
        """Raise DimMismatchError if vector_dim differs from the collection's
        stored dimension.

        Backward-compatible: collections without a ``dimension`` metadata
        key (legacy) skip validation with a debug log — there is no
        ground-truth dim to compare against.
        """
        stored = self._get_collection_dim(collection)
        if stored is None:
            logger.debug(
                "Skipping dim-validation for collection %r: no 'dimension' "
                "metadata (legacy collection).",
                collection,
            )
            return
        if vector_dim != stored:
            raise DimMismatchError(
                model_name=model_name,
                model_dim=vector_dim,
                collection_name=collection,
                collection_dim=stored,
            )

    def create_collection(self, name: str, dimension: int) -> None:
        self._client.get_or_create_collection(
            name=name,
            metadata={"dimension": dimension, "hnsw:space": "l2"},
        )
        # Refresh cache so a subsequent validate sees the new dim immediately
        # even if the collection already existed with a different stored dim.
        self._collection_dim_cache.pop(name, None)

    def has_collection(self, name: str) -> bool:
        names = [c.name for c in self._client.list_collections()]
        return name in names

    def list_collections(self) -> list[str]:
        return [c.name for c in self._client.list_collections()]

    def insert(self, collection: str, documents: list[VectorDocument]) -> None:
        missing = [d.id for d in documents if d.vector is None]
        if missing:
            raise ValueError(
                f"Documents missing pre-computed vectors: {missing}. "
                "Embed documents before inserting."
            )

        # Dedupe by ID, last-wins semantics. ChromaDB's upsert rejects intra-batch
        # duplicate IDs, but the chunker can legitimately emit chunks with identical
        # (path, start_line, end_line) tuples — e.g., AST nested nodes or overlapping
        # tree-sitter spans. Without this guard, a single duplicate aborts the entire
        # repo's final-batch insert with DuplicateIDError, losing all in-flight work.
        seen: dict[str, VectorDocument] = {}
        for d in documents:
            seen[d.id] = d  # last-wins on duplicate id
        deduped = list(seen.values())

        # Dim-validation (#46): the first vector's dim is representative since
        # an Embedder always produces a single fixed dim per call. If it differs
        # from the collection's stored dim, raise early instead of letting
        # chromadb fail mid-batch (which can lose in-flight work) or — worse —
        # silently corrupt the collection.
        if deduped:
            self._validate_dim(collection, len(deduped[0].vector or []))

        col = self._client.get_collection(name=collection)
        col.upsert(
            ids=[d.id for d in deduped],
            documents=[d.content for d in deduped],
            embeddings=[d.vector for d in deduped],
            metadatas=[d.metadata if d.metadata else None for d in deduped],
        )

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        # Dim-validation (#46): a query vector with the wrong dim against a
        # dim-locked collection produces ranked-but-meaningless results
        # (silent correctness failure). Fail fast instead.
        self._validate_dim(collection, len(vector))

        col = self._client.get_collection(name=collection)
        kwargs: dict = {
            "query_embeddings": [vector],
            "n_results": limit,
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)

        out: list[dict] = []
        ids = results["ids"][0]
        documents = results["documents"][0]
        distances = results["distances"][0]
        metadatas = results["metadatas"][0]

        for i, doc_id in enumerate(ids):
            out.append(
                {
                    "id": doc_id,
                    "content": documents[i],
                    "score": 1.0 / (1.0 + distances[i]),
                    "metadata": metadatas[i],
                }
            )
        return out

    def delete(self, collection: str, ids: list[str]) -> None:
        col = self._client.get_collection(name=collection)
        col.delete(ids=ids)

    def drop_collection(self, name: str) -> None:
        self._client.delete_collection(name=name)
        # Drop the cache entry too — a subsequent create_collection at the
        # same name with a different dim must not reuse the stale value.
        self._collection_dim_cache.pop(name, None)

    def count(self, collection: str) -> int:
        col = self._client.get_collection(name=collection)
        return col.count()

    def delete_by_metadata(self, collection: str, key: str, value: str) -> None:
        col = self._client.get_collection(name=collection)
        col.delete(where={key: value})

    def validate_all_collections(self, expected_dim: int, *, model_name: str = "<unknown>") -> None:
        """Walk every existing collection and validate its stored dim against
        ``expected_dim``. Raises DimMismatchError on the first mismatch.

        Useful at startup when the configured embed model's dim is known —
        callers can fail fast before any indexing or query work begins.
        Collections without a ``dimension`` metadata key (legacy) are
        skipped silently.
        """
        for name in self.list_collections():
            self._validate_dim(name, expected_dim, model_name=model_name)
