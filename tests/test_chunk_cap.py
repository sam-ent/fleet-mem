"""Tests for the chunk-size cap + on-400 bisect fallback (issue #29)."""

from unittest.mock import MagicMock, patch

import ollama as ollama_lib
import pytest

from fleet_mem.config import Config
from fleet_mem.embedding.ollama_embed import OllamaEmbedding
from fleet_mem.indexer import _cap_chunk_sizes, _split_file
from fleet_mem.splitter.ast_splitter import ASTChunk
from fleet_mem.splitter.text_splitter import TextChunk

# ---------------------------------------------------------------------------
# Chunker-side cap
# ---------------------------------------------------------------------------


def test_chunker_caps_oversized_input():
    """A 50_000-char single-line chunk must be split below the cap."""
    big = "x" * 50_000
    chunks = [TextChunk(content=big, start_line=1, end_line=1)]
    capped = _cap_chunk_sizes(chunks, max_chars=5000)

    assert len(capped) > 1
    for c in capped:
        assert len(c.content) <= 5000
    # All content preserved
    joined = "".join(c.content for c in capped)
    assert joined == big


def test_chunker_respects_env_override(monkeypatch, tmp_path):
    """FLEET_MEM_MAX_CHUNK_CHARS env var feeds through Config.max_chunk_chars."""
    monkeypatch.setenv("FLEET_MEM_MAX_CHUNK_CHARS", "1000")
    cfg = Config(chroma_path=tmp_path / "chroma")
    assert cfg.max_chunk_chars == 1000

    text = "a" * 5000
    chunks = _split_file(
        content=text,
        language="unknown",
        ast_languages=set(),
        max_chunk_chars=cfg.max_chunk_chars,
    )
    assert chunks
    for c in chunks:
        assert len(c.content) <= 1000


def test_chunker_splits_prefers_newline_boundaries():
    """Splitting should prefer newline boundaries near the midpoint."""
    # 6000-char content with a clear newline near the middle
    left = "a" * 2990 + "\n"
    right = "b" * 3009
    content = left + right
    chunks = [TextChunk(content=content, start_line=1, end_line=10)]
    capped = _cap_chunk_sizes(chunks, max_chars=5000)

    # Both halves should be under the cap
    for c in capped:
        assert len(c.content) <= 5000
    # And the first half should end at the newline
    assert capped[0].content.endswith("\n")


def test_chunker_preserves_ast_chunk_metadata():
    """AST chunks that are split keep their type/name metadata."""
    big_body = "def huge():\n" + ("    pass  # filler\n" * 1000)
    chunk = ASTChunk(
        content=big_body,
        start_line=1,
        end_line=1001,
        chunk_type="function",
        name="huge",
    )
    capped = _cap_chunk_sizes([chunk], max_chars=2000)
    assert len(capped) > 1
    for c in capped:
        assert isinstance(c, ASTChunk)
        assert c.chunk_type == "function"
        assert c.name == "huge"


def test_chunker_zero_cap_is_noop():
    """A cap of 0 (or negative) disables splitting."""
    content = "x" * 10_000
    chunks = [TextChunk(content=content, start_line=1, end_line=1)]
    assert _cap_chunk_sizes(chunks, max_chars=0) == chunks


# ---------------------------------------------------------------------------
# Embedder-side on-400 bisect
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path):
    return Config(chroma_path=tmp_path / "chroma")


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_embed_bisects_on_context_overflow(mock_client_cls, config):
    """A 400 'context length' on the whole batch triggers bisection."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    overflow = ollama_lib.ResponseError("input length exceeds context length", 400)

    def fake_embed(model, input):  # noqa: A002  (mock signature)
        # Fail once on batches of size >= 2, succeed on size 1.
        if len(input) >= 2:
            raise overflow
        return {"embeddings": [[0.5] * 8 for _ in input]}

    mock_client.embed.side_effect = fake_embed

    emb = OllamaEmbedding(config)
    result = emb.embed_batch(["a", "b"])

    assert len(result) == 2
    # First call was the full batch (failed), then two single-input retries.
    assert mock_client.embed.call_count == 3


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_embed_bisect_exhaustion_raises(mock_client_cls, config):
    """If 400 persists through all bisect depths, surface the error."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    overflow = ollama_lib.ResponseError("input length exceeds context length", 400)
    mock_client.embed.side_effect = overflow

    emb = OllamaEmbedding(config)
    with pytest.raises(ConnectionError) as excinfo:
        emb.embed_batch(["only-one-oversized"])

    assert "context-overflow" in str(excinfo.value) or "bisect" in str(excinfo.value)


@patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client")
def test_embed_non_overflow_400_not_bisected(mock_client_cls, config):
    """A 400 that is NOT a context-length error is raised as-is, no retry."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    other_400 = ollama_lib.ResponseError("bad request: malformed input", 400)
    mock_client.embed.side_effect = other_400

    emb = OllamaEmbedding(config)
    with pytest.raises(ConnectionError) as excinfo:
        emb.embed_batch(["a", "b"])

    assert "status=400" in str(excinfo.value)
    assert mock_client.embed.call_count == 1  # no bisect attempted
