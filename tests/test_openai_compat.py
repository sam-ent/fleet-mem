"""Tests for OpenAI-compatible embedding adapter (mocked, no API needed)."""

import pytest
from unittest.mock import MagicMock, patch

openai = pytest.importorskip("openai", reason="openai package not installed")

from src.embedding.openai_compat import OpenAICompatibleEmbedding


def _fake_response(dim: int, count: int):
    """Build a mock embeddings response."""
    resp = MagicMock()
    data = []
    for i in range(count):
        item = MagicMock()
        item.embedding = [0.1 * i] * dim
        data.append(item)
    resp.data = data
    return resp


@patch("openai.OpenAI")
def test_embed_single(mock_openai_cls):
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = _fake_response(1536, 1)
    mock_openai_cls.return_value = mock_client

    emb = OpenAICompatibleEmbedding(api_key="sk-test", model="text-embedding-3-small")
    result = emb.embed("hello")

    assert len(result) == 1536
    mock_client.embeddings.create.assert_called_once_with(
        model="text-embedding-3-small", input=["hello"]
    )


@patch("openai.OpenAI")
def test_embed_batch_chunking(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    mock_client.embeddings.create.side_effect = [
        _fake_response(1536, 64),
        _fake_response(1536, 36),
    ]

    emb = OpenAICompatibleEmbedding(api_key="sk-test")
    results = emb.embed_batch(["text"] * 100)

    assert len(results) == 100
    assert mock_client.embeddings.create.call_count == 2


@patch("openai.OpenAI")
def test_dimension_auto_detection(mock_openai_cls):
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = _fake_response(384, 1)
    mock_openai_cls.return_value = mock_client

    emb = OpenAICompatibleEmbedding(api_key="sk-test")
    dim = emb.get_dimension()

    assert dim == 384
    dim2 = emb.get_dimension()
    assert dim2 == 384
    assert mock_client.embeddings.create.call_count == 1


@patch("openai.OpenAI")
def test_get_provider(mock_openai_cls):
    mock_openai_cls.return_value = MagicMock()
    emb = OpenAICompatibleEmbedding(api_key="sk-test", model="text-embedding-3-large")
    assert emb.get_provider() == "openai-compat/text-embedding-3-large"


@patch("openai.OpenAI")
def test_default_model(mock_openai_cls):
    mock_openai_cls.return_value = MagicMock()
    emb = OpenAICompatibleEmbedding(api_key="sk-test")
    assert emb.get_provider() == "openai-compat/text-embedding-3-small"


@patch("openai.OpenAI")
def test_custom_base_url(mock_openai_cls):
    mock_openai_cls.return_value = MagicMock()
    OpenAICompatibleEmbedding(
        api_key="sk-test",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-embed",
    )
    mock_openai_cls.assert_called_once_with(
        api_key="sk-test", base_url="https://api.deepseek.com/v1"
    )


@patch("openai.OpenAI")
def test_empty_api_key(mock_openai_cls):
    """Empty API key is allowed (some local providers don't need one)."""
    mock_openai_cls.return_value = MagicMock()
    emb = OpenAICompatibleEmbedding(api_key="", model="local-model")
    assert emb.get_provider() == "openai-compat/local-model"
