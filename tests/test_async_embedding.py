"""Tests for async embedding methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Config


def _fake_embed_response(dim: int, count: int) -> dict:
    return {"embeddings": [[0.1 * i] * dim for i in range(count)]}


@pytest.fixture
def config(tmp_path):
    return Config(chroma_path=tmp_path / "chroma")


# ---------------------------------------------------------------------------
# Base class fallback
# ---------------------------------------------------------------------------


class TestBaseAsyncFallback:
    @pytest.mark.asyncio
    async def test_aembed_falls_back_to_sync(self):
        from src.embedding.base import Embedding

        class StubEmbed(Embedding):
            def embed(self, text):
                return [1.0, 2.0]

            def embed_batch(self, texts):
                return [[1.0, 2.0]] * len(texts)

            def get_dimension(self):
                return 2

            def get_provider(self):
                return "stub"

        e = StubEmbed()
        assert await e.aembed("hello") == [1.0, 2.0]
        assert await e.aembed_batch(["a", "b"]) == [[1.0, 2.0], [1.0, 2.0]]


# ---------------------------------------------------------------------------
# OllamaEmbedding async
# ---------------------------------------------------------------------------


class TestOllamaAsync:
    @pytest.mark.asyncio
    @patch("src.embedding.ollama_embed.ollama_lib.Client")
    @patch("src.embedding.ollama_embed.ollama_lib.AsyncClient")
    async def test_aembed_single(self, mock_async_cls, mock_client_cls, config):
        from src.embedding.ollama_embed import OllamaEmbedding

        mock_client_cls.return_value = MagicMock()
        mock_async = AsyncMock()
        mock_async.embed.return_value = _fake_embed_response(768, 1)
        mock_async_cls.return_value = mock_async

        emb = OllamaEmbedding(config)
        result = await emb.aembed("hello")

        assert len(result) == 768
        mock_async.embed.assert_called_once_with(model="nomic-embed-text", input=["hello"])

    @pytest.mark.asyncio
    @patch("src.embedding.ollama_embed.ollama_lib.Client")
    @patch("src.embedding.ollama_embed.ollama_lib.AsyncClient")
    async def test_aembed_batch(self, mock_async_cls, mock_client_cls, config):
        from src.embedding.ollama_embed import OllamaEmbedding

        mock_client_cls.return_value = MagicMock()
        mock_async = AsyncMock()
        mock_async.embed.side_effect = [
            _fake_embed_response(768, 64),
            _fake_embed_response(768, 36),
        ]
        mock_async_cls.return_value = mock_async

        emb = OllamaEmbedding(config)
        results = await emb.aembed_batch(["text"] * 100)

        assert len(results) == 100
        assert mock_async.embed.call_count == 2

    @pytest.mark.asyncio
    @patch("src.embedding.ollama_embed.ollama_lib.Client")
    @patch("src.embedding.ollama_embed.ollama_lib.AsyncClient")
    async def test_aembed_connection_error(self, mock_async_cls, mock_client_cls, config):
        from src.embedding.ollama_embed import OllamaEmbedding

        mock_client_cls.return_value = MagicMock()
        mock_async = AsyncMock()
        mock_async.embed.side_effect = Exception("Connection refused")
        mock_async_cls.return_value = mock_async

        emb = OllamaEmbedding(config)
        with pytest.raises(ConnectionError, match="Cannot reach Ollama"):
            await emb.aembed("hello")


# ---------------------------------------------------------------------------
# CachedEmbedding async
# ---------------------------------------------------------------------------


class TestCachedAsync:
    @pytest.mark.asyncio
    async def test_aembed_cache_hit(self, tmp_path):
        from src.embedding.cache import CachedEmbedding, EmbeddingCache

        inner = MagicMock()
        inner.get_provider.return_value = "test/model"
        inner.aembed = AsyncMock(return_value=[1.0, 2.0])

        cache = EmbeddingCache(tmp_path / "cache.db")
        cached = CachedEmbedding(inner, cache)

        # First call: miss
        result1 = await cached.aembed("hello")
        assert result1 == [1.0, 2.0]
        assert cached.cache_misses == 1
        inner.aembed.assert_called_once()

        # Second call: hit
        result2 = await cached.aembed("hello")
        assert result2 == [1.0, 2.0]
        assert cached.cache_hits == 1
        assert inner.aembed.call_count == 1  # not called again

    @pytest.mark.asyncio
    async def test_aembed_batch_partial_cache(self, tmp_path):
        from src.embedding.cache import CachedEmbedding, EmbeddingCache

        inner = MagicMock()
        inner.get_provider.return_value = "test/model"
        inner.aembed = AsyncMock(return_value=[1.0, 2.0])
        inner.aembed_batch = AsyncMock(return_value=[[3.0, 4.0]])

        cache = EmbeddingCache(tmp_path / "cache.db")
        cached = CachedEmbedding(inner, cache)

        # Seed cache with "hello"
        await cached.aembed("hello")

        # Batch with one cached, one new
        results = await cached.aembed_batch(["hello", "world"])
        assert len(results) == 2
        assert results[0] == [1.0, 2.0]  # from cache
        assert results[1] == [3.0, 4.0]  # from inner
        inner.aembed_batch.assert_called_once_with(["world"])
