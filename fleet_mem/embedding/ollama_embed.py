"""Ollama embedding adapter."""

import ollama as ollama_lib

from fleet_mem.config import Config
from fleet_mem.embedding.base import Embedding

_BATCH_CHUNK_SIZE = 64


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
        except Exception as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self._host}. "
                f"Ensure Ollama is running with model '{self._model}' pulled."
            ) from exc

        vector = response["embeddings"][0]
        if self._dimension is None:
            self._dimension = len(vector)
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts, chunked into groups of 64."""
        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_CHUNK_SIZE):
            chunk = texts[i : i + _BATCH_CHUNK_SIZE]
            try:
                response = self._client.embed(model=self._model, input=chunk)
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
