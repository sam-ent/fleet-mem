"""Tests for Ollama embedding adapter (mocked, no Ollama needed)."""

from unittest.mock import MagicMock, patch

import ollama as ollama_lib
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
def test_response_error_preserves_status_and_chain(mock_client_cls, config):
    """ResponseError from ollama must surface status + message and chain the cause."""
    mock_client = MagicMock()
    original = ollama_lib.ResponseError("input length exceeds context length", 400)
    mock_client.embed.side_effect = original
    mock_client_cls.return_value = mock_client

    emb = OllamaEmbedding(config)
    with pytest.raises(ConnectionError) as excinfo:
        emb.embed("oversized input")

    message = str(excinfo.value)
    assert "status=400" in message
    assert "input length exceeds context length" in message
    # Original ResponseError must be chained via `raise ... from err`
    assert excinfo.value.__cause__ is original
    assert isinstance(excinfo.value.__cause__, ollama_lib.ResponseError)


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_connection_level_error_still_generic(mock_client_cls, config):
    """Non-ResponseError failures still surface as 'Cannot reach Ollama'."""
    mock_client = MagicMock()
    mock_client.embed.side_effect = Exception("Connection refused")
    mock_client_cls.return_value = mock_client

    emb = OllamaEmbedding(config)
    with pytest.raises(ConnectionError, match="Cannot reach Ollama"):
        emb.embed("hello")


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_embed_batch_response_error_preserves_detail(mock_client_cls, config):
    """Batch path also preserves ResponseError status + message."""
    mock_client = MagicMock()
    original = ollama_lib.ResponseError("model not found", 404)
    mock_client.embed.side_effect = original
    mock_client_cls.return_value = mock_client

    emb = OllamaEmbedding(config)
    with pytest.raises(ConnectionError) as excinfo:
        emb.embed_batch(["a", "b"])

    assert "status=404" in str(excinfo.value)
    assert "model not found" in str(excinfo.value)
    assert excinfo.value.__cause__ is original


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_get_provider(mock_client_cls, config):
    mock_client_cls.return_value = MagicMock()
    emb = OllamaEmbedding(config)
    assert emb.get_provider() == "ollama/nomic-embed-text"


# ---------------------------------------------------------------------------
# Regression tests for issue #37 — bisect must reach size=1 + per-text fallback
# ---------------------------------------------------------------------------


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_bisect_reaches_size_one_on_large_batch(mock_client_cls, config):
    """Regression for #37: bisect must reduce batch to size 1 even when the
    initial batch is larger than the previously-hardcoded depth=3 ceiling
    would have allowed (16 > 2**3, so depth-3 bisect could only reach size 2).
    """
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    overflow = ollama_lib.ResponseError("input length exceeds context length", 400)

    def fake_embed(model, input):  # noqa: A002  (mock signature)
        # Fail any batch >= 2; succeed (single text) at size 1.
        if len(input) >= 2:
            raise overflow
        return {"embeddings": [[0.5] * 8 for _ in input]}

    mock_client.embed.side_effect = fake_embed

    emb = OllamaEmbedding(config)
    # Batch of 16 — under the old depth=3 cap this raised with
    # "after 3 bisect attempts" because the bisect could only shrink
    # 16 -> 8 -> 4 -> 2 before exhausting depth.
    result = emb.embed_batch([f"text-{i}" for i in range(16)])

    assert len(result) == 16
    for vec in result:
        assert vec == [0.5] * 8


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_bisect_isolates_single_oversized_in_batch(mock_client_cls, config):
    """A single oversized text inside an otherwise-fine batch should be
    isolated by bisection; the per-text fallback fires for the offender,
    and the rest succeed normally.
    """
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    overflow = ollama_lib.ResponseError("input length exceeds context length", 400)
    oversized = "X" * 400

    def fake_embed(model, input):  # noqa: A002
        # If the batch contains the oversized text AND has size > 1, fail.
        # Once isolated (size 1), the per-text fallback splits it into halves
        # of 200 chars each, which we accept.
        if oversized in input and len(input) > 1:
            raise overflow
        if len(input) == 1 and input[0] == oversized:
            # Whole-text size-1 retry still fails — forces text-split fallback.
            raise overflow
        return {"embeddings": [[0.1] * 4 for _ in input]}

    mock_client.embed.side_effect = fake_embed

    emb = OllamaEmbedding(config)
    inputs = ["ok-1", "ok-2", oversized, "ok-3"]
    result = emb.embed_batch(inputs)

    assert len(result) == 4
    # All vectors are the right dimension.
    for vec in result:
        assert len(vec) == 4


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_text_split_fallback_averages_halves(mock_client_cls, config):
    """A single oversized text passed alone triggers the text-split fallback:
    the text is halved, each half embedded, and the mean vector returned.
    """
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    overflow = ollama_lib.ResponseError("input length exceeds context length", 400)
    left_half = "A" * 32
    right_half = "B" * 32
    text = left_half + right_half

    def fake_embed(model, input):  # noqa: A002
        # Fail the full text; succeed on its halves with distinct vectors
        # so we can verify the mean was computed.
        if len(input) == 1 and input[0] == text:
            raise overflow
        if len(input) == 1 and input[0] == left_half:
            return {"embeddings": [[1.0, 1.0, 1.0, 1.0]]}
        if len(input) == 1 and input[0] == right_half:
            return {"embeddings": [[3.0, 3.0, 3.0, 3.0]]}
        return {"embeddings": [[0.0] * 4 for _ in input]}

    mock_client.embed.side_effect = fake_embed

    emb = OllamaEmbedding(config)
    result = emb.embed_batch([text])

    assert len(result) == 1
    # Mean of [1,1,1,1] and [3,3,3,3] is [2,2,2,2].
    assert result[0] == [2.0, 2.0, 2.0, 2.0]
