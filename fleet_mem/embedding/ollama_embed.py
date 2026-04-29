"""Ollama embedding adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import ollama as ollama_lib

from fleet_mem.config import Config
from fleet_mem.embedding.base import Embedding

if TYPE_CHECKING:
    from tokenizers import Tokenizer

_BATCH_CHUNK_SIZE = 64

# Minimum text length below which we stop trying to recover an oversized
# single input. At or below this length, an on-400 from Ollama is treated
# as unrecoverable and surfaced rather than recursed further.
_MIN_TEXT_BISECT_CHARS = 1

# Mapping from Ollama embed model names (with optional ``:tag`` suffix
# stripped) to HuggingFace tokenizer ids. Used by ``get_tokenizer`` for
# the token-aware chunk-cap path (issue #42). Keep small; unknown models
# fall back to char-cap by returning ``None``. Adding a model here is a
# lightweight way to enable token-aware capping for it; the assumption is
# that the Ollama distribution and the HF model use the same tokenizer.
_OLLAMA_TO_HF_TOKENIZER: dict[str, str] = {
    "all-minilm": "sentence-transformers/all-MiniLM-L6-v2",
    "nomic-embed-text": "nomic-ai/nomic-embed-text-v1",
    "mxbai-embed-large": "mixedbread-ai/mxbai-embed-large-v1",
    "bge-large": "BAAI/bge-large-en-v1.5",
    "bge-m3": "BAAI/bge-m3",
    "snowflake-arctic-embed": "Snowflake/snowflake-arctic-embed-l",
}

_logger = logging.getLogger(__name__)


def _is_context_overflow(err: ollama_lib.ResponseError) -> bool:
    """True if the ResponseError looks like a context-window overflow."""
    if err.status_code != 400:
        return False
    msg = str(err).lower()
    return "context length" in msg or "input length" in msg or "too long" in msg


class OllamaEmbedding(Embedding):
    """Embedding provider using Ollama's embed API."""

    def __init__(self, config: Config | None = None):
        cfg = config or Config()
        self._model = cfg.ollama_embed_model
        self._host = cfg.ollama_host
        self._client = ollama_lib.Client(host=self._host)
        self._dimension: int | None = None
        # Cached tokenizer for the token-aware chunk-cap path (issue #42).
        # ``None`` is a valid cached state meaning "no tokenizer available".
        # We use a sentinel to distinguish "not yet attempted" from "tried
        # and gave up" so we don't pay the load cost or log warnings twice.
        self._tokenizer_loaded: bool = False
        self._tokenizer: Any | None = None

    def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        try:
            response = self._client.embed(model=self._model, input=[text])
        except ollama_lib.ResponseError as err:
            raise ConnectionError(
                f"Ollama request failed for model '{self._model}' (status={err.status_code}): {err}"
            ) from err
        except Exception as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self._host}. "
                f"Ensure Ollama is running with model '{self._model}' pulled."
            ) from exc

        vector = response["embeddings"][0]
        if self._dimension is None:
            self._dimension = len(vector)
        return vector

    def _embed_inputs(self, inputs: list[str], depth: int = 0) -> list[list[float]]:
        """Call Ollama with a list of inputs. On a context-length 400,
        bisect the batch and retry each half until the batch is reduced
        to a single input, then split the text itself.

        Recursion uses size-based termination rather than a fixed depth
        ceiling: batches halve until size 1 (worst case ``log2(batch_size)``
        recursions), then the single oversized text is split in half on
        a safe boundary and each half is embedded; the mean vector is
        returned so downstream storage remains consistent. Text-level
        recursion terminates when the half cannot be bisected further
        (``len(text) <= _MIN_TEXT_BISECT_CHARS``), at which point the
        original 400 is surfaced.
        """
        if not inputs:
            return []
        try:
            response = self._client.embed(model=self._model, input=inputs)
        except ollama_lib.ResponseError as err:
            if _is_context_overflow(err):
                if len(inputs) > 1:
                    mid = len(inputs) // 2
                    _logger.warning(
                        "ollama: context-length 400 on batch of %d; bisecting (depth=%d)",
                        len(inputs),
                        depth,
                    )
                    left = self._embed_inputs(inputs[:mid], depth + 1)
                    right = self._embed_inputs(inputs[mid:], depth + 1)
                    return left + right
                # Single oversized input: split the text itself.
                text = inputs[0]
                if len(text) <= _MIN_TEXT_BISECT_CHARS:
                    raise ConnectionError(
                        f"Ollama rejected input of length {len(text)} "
                        f"as context-overflow; cannot bisect further: {err}"
                    ) from err
                mid = len(text) // 2
                _logger.warning(
                    "ollama: context-length 400 on single input (len=%d); "
                    "splitting text and averaging (depth=%d)",
                    len(text),
                    depth,
                )
                left_vecs = self._embed_inputs([text[:mid]], depth + 1)
                right_vecs = self._embed_inputs([text[mid:]], depth + 1)
                if not left_vecs or not right_vecs:
                    raise ConnectionError(
                        f"Ollama bisect recovery produced no vectors for "
                        f"oversized input (len={len(text)}): {err}"
                    ) from err
                averaged = [(a + b) / 2.0 for a, b in zip(left_vecs[0], right_vecs[0])]
                return [averaged]
            raise ConnectionError(
                f"Ollama request failed for model '{self._model}' (status={err.status_code}): {err}"
            ) from err
        except Exception as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self._host}. "
                f"Ensure Ollama is running with model '{self._model}' pulled."
            ) from exc

        embeddings = response["embeddings"]
        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])
        return embeddings

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts, chunked into groups of 64.

        If Ollama returns an HTTP 400 indicating a context-window
        overflow, the offending batch is recursively bisected down to
        a single input, after which the offending text itself is split
        in half and the mean vector is returned. This defense-in-depth
        fallback handles oversized inputs that slipped past the
        chunker's character cap.
        """
        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_CHUNK_SIZE):
            chunk = texts[i : i + _BATCH_CHUNK_SIZE]
            results.extend(self._embed_inputs(chunk))
        return results

    def get_dimension(self) -> int:
        """Return embedding dimension, auto-detecting on first call."""
        if self._dimension is None:
            self.embed("dimension probe")
        return self._dimension

    def get_provider(self) -> str:
        return f"ollama/{self._model}"

    def get_tokenizer(self) -> "Tokenizer | None":
        """Lazy-load and return the HF tokenizer for ``self._model``, or None.

        The mapping from Ollama model name to HF tokenizer id lives in
        ``_OLLAMA_TO_HF_TOKENIZER``. Returns ``None`` (and warns once) if:
          - the ``tokenizers`` package isn't installed (optional dep);
          - the active model isn't in the mapping;
          - the tokenizer can't be fetched (network error / no cached
            tokenizer.json on disk in offline environments).

        Result is cached so loading is attempted at most once per
        ``OllamaEmbedding`` instance. Callers should treat ``None`` as
        "fall back to char-cap".
        """
        if self._tokenizer_loaded:
            return self._tokenizer
        self._tokenizer_loaded = True

        # Strip ``:tag`` suffix (e.g. ``nomic-embed-text:latest`` -> ``nomic-embed-text``).
        base = self._model.split(":", 1)[0]
        hf_id = _OLLAMA_TO_HF_TOKENIZER.get(base)
        if hf_id is None:
            _logger.info(
                "ollama: no HF tokenizer mapping for model '%s'; "
                "token-aware chunk cap unavailable, falling back to char-cap",
                self._model,
            )
            return None

        try:
            from tokenizers import Tokenizer
        except ImportError:
            _logger.info(
                "ollama: 'tokenizers' package not installed; install "
                "fleet-mem[tokenizer-aware] to enable token-aware chunk cap"
            )
            return None

        try:
            self._tokenizer = Tokenizer.from_pretrained(hf_id)
        except Exception as exc:
            # Network-blocked / auth-required / HF outage / model not on hub.
            _logger.warning(
                "ollama: failed to load HF tokenizer '%s' for model '%s' "
                "(%s); falling back to char-cap",
                hf_id,
                self._model,
                exc,
            )
            self._tokenizer = None
        return self._tokenizer

    async def aembed(self, text: str) -> list[float]:
        """Async embed a single text string."""
        async_client = ollama_lib.AsyncClient(host=self._host)
        try:
            response = await async_client.embed(model=self._model, input=[text])
        except ollama_lib.ResponseError as err:
            raise ConnectionError(
                f"Ollama request failed for model '{self._model}' (status={err.status_code}): {err}"
            ) from err
        except Exception as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self._host}. "
                f"Ensure Ollama is running with model '{self._model}' pulled."
            ) from exc

        vector = response["embeddings"][0]
        if self._dimension is None:
            self._dimension = len(vector)
        return vector

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        """Async embed multiple texts, chunked into groups of 64."""
        async_client = ollama_lib.AsyncClient(host=self._host)
        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_CHUNK_SIZE):
            chunk = texts[i : i + _BATCH_CHUNK_SIZE]
            try:
                response = await async_client.embed(model=self._model, input=chunk)
            except ollama_lib.ResponseError as err:
                raise ConnectionError(
                    f"Ollama request failed for model '{self._model}' "
                    f"(status={err.status_code}): {err}"
                ) from err
            except Exception as exc:
                raise ConnectionError(
                    f"Cannot reach Ollama at {self._host}. "
                    f"Ensure Ollama is running with model '{self._model}' pulled."
                ) from exc

            embeddings = response["embeddings"]
            results.extend(embeddings)
            if self._dimension is None and embeddings:
                self._dimension = len(embeddings[0])
        return results
