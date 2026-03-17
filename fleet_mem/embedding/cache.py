"""SQLite-backed embedding vector cache."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import xxhash

from .base import Embedding


class EmbeddingCache:
    """SQLite-backed embedding vector cache."""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS embedding_cache (
                content_hash TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                vector BLOB NOT NULL,
                dimension INT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (content_hash, provider, model)
            )"""
        )
        self._conn.commit()

    def get(self, content_hash: str, provider: str, model: str) -> list[float] | None:
        """Lookup by content_hash + provider + model. Return None on miss."""
        row = self._conn.execute(
            "SELECT vector, dimension FROM embedding_cache"
            " WHERE content_hash=? AND provider=? AND model=?",
            (content_hash, provider, model),
        ).fetchone()
        if row is None:
            return None
        blob, dimension = row
        return list(struct.unpack(f"{dimension}f", blob))

    def put(self, content_hash: str, vector: list[float], provider: str, model: str):
        """Store vector as BLOB. INSERT OR REPLACE."""
        blob = struct.pack(f"{len(vector)}f", *vector)
        self._conn.execute(
            "INSERT OR REPLACE INTO embedding_cache"
            " (content_hash, provider, model, vector, dimension)"
            " VALUES (?, ?, ?, ?, ?)",
            (content_hash, provider, model, blob, len(vector)),
        )
        self._conn.commit()

    def clear(self):
        """Delete all cached embeddings."""
        self._conn.execute("DELETE FROM embedding_cache")
        self._conn.commit()


class CachedEmbedding(Embedding):
    """Wraps any Embedding with a disk cache."""

    def __init__(self, inner: Embedding, cache: EmbeddingCache):
        self._inner = inner
        self._cache = cache
        self._hits = 0
        self._misses = 0

    @property
    def cache_hits(self) -> int:
        return self._hits

    @property
    def cache_misses(self) -> int:
        return self._misses

    def _hash(self, text: str) -> str:
        return xxhash.xxh3_64(text.encode()).hexdigest()

    def embed(self, text: str) -> list[float]:
        h = self._hash(text)
        provider = self._inner.get_provider()
        cached = self._cache.get(h, provider, "")
        if cached is not None:
            self._hits += 1
            return cached
        self._misses += 1
        vec = self._inner.embed(text)
        self._cache.put(h, vec, provider, "")
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        provider = self._inner.get_provider()
        hashes = [self._hash(t) for t in texts]

        # Check cache for all
        results: list[list[float] | None] = []
        miss_indices: list[int] = []
        miss_texts: list[str] = []

        for i, (h, t) in enumerate(zip(hashes, texts)):
            cached = self._cache.get(h, provider, "")
            if cached is not None:
                self._hits += 1
                results.append(cached)
            else:
                self._misses += 1
                results.append(None)
                miss_indices.append(i)
                miss_texts.append(t)

        if miss_texts:
            fresh = self._inner.embed_batch(miss_texts)
            for idx, vec in zip(miss_indices, fresh):
                results[idx] = vec
                self._cache.put(hashes[idx], vec, provider, "")

        return results  # type: ignore[return-value]

    async def aembed(self, text: str) -> list[float]:
        """Async embed with cache."""
        h = self._hash(text)
        provider = self._inner.get_provider()
        cached = self._cache.get(h, provider, "")
        if cached is not None:
            self._hits += 1
            return cached
        self._misses += 1
        vec = await self._inner.aembed(text)
        self._cache.put(h, vec, provider, "")
        return vec

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        """Async batch embed with cache."""
        provider = self._inner.get_provider()
        hashes = [self._hash(t) for t in texts]

        results: list[list[float] | None] = []
        miss_indices: list[int] = []
        miss_texts: list[str] = []

        for i, (h, t) in enumerate(zip(hashes, texts)):
            cached = self._cache.get(h, provider, "")
            if cached is not None:
                self._hits += 1
                results.append(cached)
            else:
                self._misses += 1
                results.append(None)
                miss_indices.append(i)
                miss_texts.append(t)

        if miss_texts:
            fresh = await self._inner.aembed_batch(miss_texts)
            for idx, vec in zip(miss_indices, fresh):
                results[idx] = vec
                self._cache.put(hashes[idx], vec, provider, "")

        return results  # type: ignore[return-value]

    def get_dimension(self) -> int:
        return self._inner.get_dimension()

    def get_provider(self) -> str:
        return self._inner.get_provider()
