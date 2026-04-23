"""Ollama embedding adapter."""

import logging

import ollama as ollama_lib

from fleet_mem.config import Config
from fleet_mem.embedding.base import Embedding

_BATCH_CHUNK_SIZE = 64

# Max depth for the on-400 bisect fallback. Beyond this, the offending
# input is skipped with a zero vector placeholder rather than aborting
# the whole indexing run.
_MAX_BISECT_DEPTH = 3

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
        bisect the batch and retry each half, bounded by ``_MAX_BISECT_DEPTH``.

        When the batch has a single oversized input and bisection cannot
        shrink it further (or depth is exhausted), the single text itself
        is split in half character-wise and each half is embedded; the
        mean vector is returned so downstream storage remains consistent.
        """
        if not inputs:
            return []
        try:
            response = self._client.embed(model=self._model, input=inputs)
        except ollama_lib.ResponseError as err:
            if _is_context_overflow(err):
                if depth >= _MAX_BISECT_DEPTH:
                    raise ConnectionError(
                        f"Ollama request failed for model '{self._model}' "
                        f"(status={err.status_code}) after {depth} bisect attempts: {err}"
                    ) from err
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
                if len(text) <= 1:
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
        overflow, the offending batch is recursively bisected (up to
        ``_MAX_BISECT_DEPTH``) as a defense-in-depth fallback against
        oversized inputs that slipped past the chunker's character cap.
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
