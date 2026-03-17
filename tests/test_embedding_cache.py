"""Tests for embedding cache layer."""

from unittest.mock import MagicMock

import pytest

from fleet_mem.embedding.cache import CachedEmbedding, EmbeddingCache


@pytest.fixture
def cache(tmp_path):
    return EmbeddingCache(tmp_path / "test_cache.db")


@pytest.fixture
def mock_embedder():
    emb = MagicMock()
    emb.get_provider.return_value = "ollama/nomic-embed-text"
    emb.get_dimension.return_value = 4
    emb.embed.return_value = [0.1, 0.2, 0.3, 0.4]
    emb.embed_batch.return_value = [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
    return emb


def test_cache_miss_calls_inner(cache, mock_embedder):
    cached = CachedEmbedding(mock_embedder, cache)
    result = cached.embed("hello")

    assert result == [0.1, 0.2, 0.3, 0.4]
    mock_embedder.embed.assert_called_once_with("hello")


def test_cache_hit_skips_inner(cache, mock_embedder):
    cached = CachedEmbedding(mock_embedder, cache)
    cached.embed("hello")
    mock_embedder.embed.reset_mock()

    result = cached.embed("hello")

    assert result == pytest.approx([0.1, 0.2, 0.3, 0.4], abs=1e-6)
    mock_embedder.embed.assert_not_called()


def test_batch_mixed_hits_misses(cache, mock_embedder):
    cached = CachedEmbedding(mock_embedder, cache)

    # Pre-populate cache for "hello"
    cached.embed("hello")
    mock_embedder.embed.reset_mock()

    # Batch with one hit ("hello") and one miss ("world")
    mock_embedder.embed_batch.return_value = [[0.5, 0.6, 0.7, 0.8]]

    results = cached.embed_batch(["hello", "world"])

    assert len(results) == 2
    assert results[0] == pytest.approx([0.1, 0.2, 0.3, 0.4], abs=1e-6)  # from cache
    assert results[1] == pytest.approx([0.5, 0.6, 0.7, 0.8], abs=1e-6)  # from inner
    # Only "world" should be sent to inner
    mock_embedder.embed_batch.assert_called_once_with(["world"])


def test_different_provider_is_cache_miss(tmp_path):
    cache = EmbeddingCache(tmp_path / "test.db")

    # Store with one provider
    cache.put("hash1", [1.0, 2.0], "provider_a", "model_a")

    # Lookup with different provider = miss
    assert cache.get("hash1", "provider_b", "model_a") is None
    # Lookup with different model = miss
    assert cache.get("hash1", "provider_a", "model_b") is None
    # Same provider+model = hit
    assert cache.get("hash1", "provider_a", "model_a") == [1.0, 2.0]


def test_clear_wipes_cache(cache, mock_embedder):
    cached = CachedEmbedding(mock_embedder, cache)
    cached.embed("hello")
    mock_embedder.embed.reset_mock()

    cache.clear()

    cached.embed("hello")
    mock_embedder.embed.assert_called_once_with("hello")


def test_get_dimension_delegates(cache, mock_embedder):
    cached = CachedEmbedding(mock_embedder, cache)
    assert cached.get_dimension() == 4


def test_get_provider_delegates(cache, mock_embedder):
    cached = CachedEmbedding(mock_embedder, cache)
    assert cached.get_provider() == "ollama/nomic-embed-text"
