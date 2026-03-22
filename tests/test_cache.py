import sqlite3
import struct
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest
import xxhash

from fleet_mem.embedding.cache import EmbeddingCache, CachedEmbedding
from fleet_mem.embedding.base import Embedding


@pytest.fixture
def tmp_db_path(tmp_path):
    """Fixture for a temporary database path."""
    return tmp_path / "test_embeddings.db"


@pytest.fixture
def cache(tmp_db_path):
    """Fixture for a clean EmbeddingCache instance."""
    return EmbeddingCache(tmp_db_path)


@pytest.fixture
def mock_inner_embedding():
    """Fixture for a mocked Embedding provider."""
    mock = MagicMock(spec=Embedding)
    mock.get_provider.return_value = "test-provider"
    mock.get_dimension.return_value = 4
    
    # Mock synchronous methods
    mock.embed.side_effect = lambda t: [float(ord(c)) for c in t[:4]] if len(t) >= 4 else [1.0, 2.0, 3.0, 4.0]
    mock.embed_batch.side_effect = lambda ts: [mock.embed(t) for t in ts]
    
    # Mock asynchronous methods
    async def aembed(t):
        return mock.embed(t)
    
    async def aembed_batch(ts):
        return [await aembed(t) for t in ts]
        
    mock.aembed = AsyncMock(side_effect=aembed)
    mock.aembed_batch = AsyncMock(side_effect=aembed_batch)
    return mock


class TestEmbeddingCache:
    def test_init_creates_database_and_table(self, tmp_path):
        db_path = tmp_path / "subdir" / "cache.db"
        ec = EmbeddingCache(db_path)
        assert db_path.exists()
        
        # Verify schema
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("PRAGMA table_info(embedding_cache)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {"content_hash", "provider", "model", "vector", "dimension", "created_at"}
        assert expected.issubset(columns)
        conn.close()

    def test_put_and_get(self, cache):
        vector = [0.1, 0.2, 0.3, 0.4]
        content_hash = "hash_123"
        provider = "prov"
        model = "mod"
        
        cache.put(content_hash, vector, provider, model)
        result = cache.get(content_hash, provider, model)
        
        assert result == pytest.approx(vector)
        assert cache.get("non_existent", provider, model) is None

    def test_put_replace(self, cache):
        content_hash = "h"
        provider = "p"
        model = "m"
        
        cache.put(content_hash, [1.0], provider, model)
        cache.put(content_hash, [2.0], provider, model)
        
        assert cache.get(content_hash, provider, model) == [2.0]

    def test_clear(self, cache):
        cache.put("h1", [1.0], "p", "m")
        cache.put("h2", [2.0], "p", "m")
        cache.clear()
        
        assert cache.get("h1", "p", "m") is None
        assert cache.get("h2", "p", "m") is None


class TestCachedEmbedding:
    def test_metadata_delegation(self, mock_inner_embedding, cache):
        ce = CachedEmbedding(mock_inner_embedding, cache)
        assert ce.get_dimension() == 4
        assert ce.get_provider() == "test-provider"
        assert ce.cache_hits == 0
        assert ce.cache_misses == 0

    def test_embed_hit_miss_logic(self, mock_inner_embedding, cache):
        ce = CachedEmbedding(mock_inner_embedding, cache)
        text = "hello world"
        
        # First call: Cache Miss
        vec1 = ce.embed(text)
        assert ce.cache_misses == 1
        assert ce.cache_hits == 0
        mock_inner_embedding.embed.assert_called_once_with(text)
        
        # Second call: Cache Hit
        mock_inner_embedding.embed.reset_mock()
        vec2 = ce.embed(text)
        assert vec2 == vec1
        assert ce.cache_misses == 1
        assert ce.cache_hits == 1
        mock_inner_embedding.embed.assert_not_called()

    def test_embed_batch_mixed_hits(self, mock_inner_embedding, cache):
        ce = CachedEmbedding(mock_inner_embedding, cache)
        
        # Seed cache with "text1"
        ce.embed("text1")
        mock_inner_embedding.embed_batch.reset_mock()
        assert ce.cache_misses == 1
        
        # Batch call with "text1" (hit) and "text2" (miss)
        results = ce.embed_batch(["text1", "text2"])
        
        assert len(results) == 2
        assert ce.cache_hits == 1
        assert ce.cache_misses == 2
        # Should only request "text2" from inner provider
        mock_inner_embedding.embed_batch.assert_called_once_with(["text2"])

    @pytest.mark.asyncio
    async def test_aembed(self, mock_inner_embedding, cache):
        ce = CachedEmbedding(mock_inner_embedding, cache)
        text = "async_text"
        
        # Miss
        vec1 = await ce.aembed(text)
        assert ce.cache_misses == 1
        mock_inner_embedding.aembed.assert_called_once_with(text)
        
        # Hit
        mock_inner_embedding.aembed.reset_mock()
        vec2 = await ce.aembed(text)
        assert vec2 == vec1
        assert ce.cache_hits == 1
        mock_inner_embedding.aembed.assert_not_called()

    @pytest.mark.asyncio
    async def test_aembed_batch(self, mock_inner_embedding, cache):
        ce = CachedEmbedding(mock_inner_embedding, cache)
        
        # Seed cache
        await ce.aembed("a1")
        mock_inner_embedding.aembed_batch.reset_mock()
        
        # Mixed batch
        results = await ce.aembed_batch(["a1", "a2", "a3"])
        
        assert len(results) == 3
        assert ce.cache_hits == 1
        assert ce.cache_misses == 3 # 1 (a1) + 2 (a2, a3)
        mock_inner_embedding.aembed_batch.assert_called_once_with(["a2", "a3"])

    def test_hash_consistency(self, mock_inner_embedding, cache):
        ce = CachedEmbedding(mock_inner_embedding, cache)
        text = "consistency"
        h1 = ce._hash(text)
        h2 = xxhash.xxh3_64(text.encode()).hexdigest()
        assert h1 == h2

    def test_different_providers_isolation(self, mock_inner_embedding, cache):
        # Setup two embeddings with different providers
        ce1 = CachedEmbedding(mock_inner_embedding, cache)
        
        mock2 = MagicMock(spec=Embedding)
        mock2.get_provider.return_value = "other-provider"
        mock2.embed.return_value = [9.0, 9.0, 9.0, 9.0]
        ce2 = CachedEmbedding(mock2, cache)
        
        text = "shared_text"
        
        v1 = ce1.embed(text)
        v2 = ce2.embed(text)
        
        assert v1 != v2
        assert ce1.cache_misses == 1
        assert ce2.cache_misses == 1
        assert cache.get(ce1._hash(text), "test-provider", "") == v1
        assert cache.get(ce2._hash(text), "other-provider", "") == v2
