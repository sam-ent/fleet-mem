"""Tests for Ollama embedding adapter (mocked, no Ollama needed)."""

from unittest.mock import MagicMock, patch

import pytest

from fleet_mem.config import Config
from fleet_mem.embedding.ollama_embed import OllamaEmbedding


def _fake_embed_response(dim: int, count: int) -> dict:
    return {"embeddings": [[0.1 * i] * dim for i in range(count)]}


@pytest.fixture
def config(tmp_path):
    return Config(chroma_path=tmp_path / "chroma")


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_embed_single(mock_client_cls, config):
    mock_client = MagicMock()
    mock_client.embed.return_value = _fake_embed_response(768, 1)
    mock_client_cls.return_value = mock_client

    emb = OllamaEmbedding(config)
    result = emb.embed("hello")

    assert len(result) == 768
    mock_client.embed.assert_called_once_with(model="nomic-embed-text", input=["hello"])


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_embed_batch_chunking(mock_client_cls, config):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # 100 texts should be split into chunks of 64 + 36
    mock_client.embed.side_effect = [
        _fake_embed_response(768, 64),
        _fake_embed_response(768, 36),
    ]

    emb = OllamaEmbedding(config)
    results = emb.embed_batch(["text"] * 100)

    assert len(results) == 100
    assert mock_client.embed.call_count == 2


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_dimension_auto_detection(mock_client_cls, config):
    mock_client = MagicMock()
    mock_client.embed.return_value = _fake_embed_response(384, 1)
    mock_client_cls.return_value = mock_client

    emb = OllamaEmbedding(config)
    dim = emb.get_dimension()

    assert dim == 384
    # Second call should use cached value, no extra embed call
    dim2 = emb.get_dimension()
    assert dim2 == 384
    assert mock_client.embed.call_count == 1


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_connection_error(mock_client_cls, config):
    mock_client = MagicMock()
    mock_client.embed.side_effect = Exception("Connection refused")
    mock_client_cls.return_value = mock_client

    emb = OllamaEmbedding(config)
    with pytest.raises(ConnectionError, match="Cannot reach Ollama"):
        emb.embed("hello")


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_get_provider(mock_client_cls, config):
    mock_client_cls.return_value = MagicMock()
    emb = OllamaEmbedding(config)
    assert emb.get_provider() == "ollama/nomic-embed-text"
