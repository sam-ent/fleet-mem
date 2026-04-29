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


# ---------------------------------------------------------------------------
# Token-aware chunk cap (issue #42)
# ---------------------------------------------------------------------------


class _FakeEncoding:
    """Minimal stand-in for HF ``tokenizers.Encoding`` (only ``.ids`` used)."""

    def __init__(self, ids: list[int]):
        self.ids = ids


class _FakeTokenizer:
    """Minimal stand-in for HF ``tokenizers.Tokenizer`` for unit tests.

    Treats every non-whitespace character as 2 tokens — far stricter than a
    real BPE tokenizer, which simulates dense content (where char-cap
    underestimates token count) without depending on a real tokenizer.json.
    """

    def __init__(self, tokens_per_char: int = 2):
        self.tokens_per_char = tokens_per_char

    def encode(self, text: str) -> _FakeEncoding:
        # 2 tokens per non-whitespace char; 1 per whitespace.
        n = sum(self.tokens_per_char if not c.isspace() else 1 for c in text)
        return _FakeEncoding(ids=list(range(n)))


def test_token_aware_cap_when_tokenizer_loaded():
    """A dense-content chunk that fits the char-cap but blows the token-cap
    must be subdivided until every piece fits the token-cap (issue #42)."""
    tok = _FakeTokenizer(tokens_per_char=2)
    # 600 chars of dense content -> 1200 tokens with the fake tokenizer.
    # Char-cap of 1000 alone would let this through; token-cap of 200 is
    # the real bound that has to be enforced.
    dense = "x" * 600
    chunks = [TextChunk(content=dense, start_line=1, end_line=1)]

    capped = _cap_chunk_sizes(
        chunks,
        max_chars=1000,
        tokenizer=tok,
        max_tokens=200,
    )

    assert len(capped) > 1, "expected token-cap to force subdivision"
    for c in capped:
        # Every produced chunk must be within the token-cap.
        token_count = len(tok.encode(c.content).ids)
        assert token_count <= 200, f"chunk has {token_count} tokens, exceeds cap=200"
    # Round-tripping the content: the split is non-lossy.
    assert "".join(c.content for c in capped) == dense


def test_falls_back_to_char_cap_when_tokenizer_unavailable():
    """If the embedder's get_tokenizer() returns None, the indexer must
    keep using the char-cap (no crash, no over-aggressive splitting)."""
    big = "x" * 50_000
    chunks = [TextChunk(content=big, start_line=1, end_line=1)]

    # tokenizer=None simulates "tokenizers package not installed" or
    # "model not in mapping" or "HF load failed" — all the documented
    # fallback paths in OllamaEmbedding.get_tokenizer().
    capped = _cap_chunk_sizes(
        chunks,
        max_chars=5000,
        tokenizer=None,
        max_tokens=200,  # ignored when tokenizer is None
    )

    assert len(capped) > 1
    for c in capped:
        assert len(c.content) <= 5000
    assert "".join(c.content for c in capped) == big


def test_char_cap_alone_unchanged():
    """Old config (no max_chunk_tokens) must produce identical behavior to
    pre-#42 — proves backward compatibility."""
    big = "x" * 50_000
    chunks = [TextChunk(content=big, start_line=1, end_line=1)]

    # Default kwargs (tokenizer=None, max_tokens=None): exactly the
    # signature pre-#42 callers used.
    capped_new = _cap_chunk_sizes(chunks, max_chars=5000)
    capped_legacy = _cap_chunk_sizes(chunks, max_chars=5000, tokenizer=None, max_tokens=None)

    assert len(capped_new) == len(capped_legacy)
    for a, b in zip(capped_new, capped_legacy):
        assert a.content == b.content
    for c in capped_new:
        assert len(c.content) <= 5000


def test_token_cap_takes_precedence_over_char_cap_when_stricter():
    """Both caps configured + token-cap is the stricter bound: every
    output chunk must satisfy BOTH caps."""
    tok = _FakeTokenizer(tokens_per_char=2)
    # 4000 chars of dense content -> 8000 tokens. Char-cap of 5000 alone
    # is fine, but token-cap of 100 forces aggressive subdivision.
    dense = "x" * 4000
    chunks = [TextChunk(content=dense, start_line=1, end_line=1)]

    capped = _cap_chunk_sizes(
        chunks,
        max_chars=5000,
        tokenizer=tok,
        max_tokens=100,
    )

    for c in capped:
        assert len(c.content) <= 5000  # char-cap
        token_count = len(tok.encode(c.content).ids)
        assert token_count <= 100  # token-cap (stricter)


def test_split_file_threads_tokenizer_through():
    """_split_file must apply the token-cap when both tokenizer and
    max_chunk_tokens are supplied via kwargs (the path indexer/server uses)."""
    tok = _FakeTokenizer(tokens_per_char=2)
    # 800 chars => 1600 tokens; well under char-cap=10000 but well over
    # token-cap=200, so token-aware splitting is what catches it.
    text = "y" * 800
    chunks = _split_file(
        content=text,
        language="unknown",
        ast_languages=set(),
        max_chunk_chars=10000,
        tokenizer=tok,
        max_chunk_tokens=200,
    )
    assert chunks
    for c in chunks:
        assert len(tok.encode(c.content).ids) <= 200


def test_ollama_embedding_get_tokenizer_default_no_mapping(config):
    """OllamaEmbedding.get_tokenizer() returns None for unknown models
    (graceful char-cap fallback, no exception)."""
    config.ollama_embed_model = "totally-made-up-model-xyz"
    with patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client"):
        emb = OllamaEmbedding(config)
        assert emb.get_tokenizer() is None
        # Cached: a second call hits the cached None, no log spam.
        assert emb.get_tokenizer() is None


def test_ollama_embedding_get_tokenizer_handles_load_failure(config):
    """If Tokenizer.from_pretrained raises (network / no cached file),
    get_tokenizer returns None rather than propagating — keeps char-cap usable."""
    config.ollama_embed_model = "all-minilm"
    with patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client"):
        emb = OllamaEmbedding(config)
        with patch(
            "tokenizers.Tokenizer.from_pretrained",
            side_effect=RuntimeError("simulated network failure"),
        ):
            assert emb.get_tokenizer() is None


def test_ollama_embedding_get_tokenizer_strips_tag(config):
    """``model:tag`` (e.g. ``nomic-embed-text:latest``) maps the same as
    bare ``nomic-embed-text``. Returned object is whatever the patched
    Tokenizer.from_pretrained yields — we just check the mapping path."""
    config.ollama_embed_model = "all-minilm:latest"
    sentinel = object()
    with patch("fleet_mem.embedding.ollama_embed.ollama_lib.Client"):
        emb = OllamaEmbedding(config)
        with patch("tokenizers.Tokenizer.from_pretrained", return_value=sentinel) as m:
            result = emb.get_tokenizer()
            assert result is sentinel
            # Called with the bare HF id (suffix stripped, mapping applied).
            m.assert_called_once_with("sentence-transformers/all-MiniLM-L6-v2")


def test_default_embedding_get_tokenizer_returns_none():
    """The base ``Embedding`` class default returns None — providers
    that don't override ``get_tokenizer`` keep working unchanged.

    Uses a minimal in-test subclass to avoid pulling in optional
    third-party deps (e.g. ``openai``) just to exercise the default.
    """
    from fleet_mem.embedding.base import Embedding

    class _MinimalEmbedding(Embedding):
        def embed(self, text: str) -> list[float]:
            return [0.0]

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] for _ in texts]

        def get_dimension(self) -> int:
            return 1

        def get_provider(self) -> str:
            return "test/minimal"

    emb = _MinimalEmbedding()
    # Default ABC implementation returns None — no override needed.
    assert emb.get_tokenizer() is None
