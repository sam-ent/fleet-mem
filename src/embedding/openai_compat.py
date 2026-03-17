"""OpenAI-compatible embedding adapter."""

from __future__ import annotations

import os

from src.embedding.base import Embedding

_BATCH_CHUNK_SIZE = 64


class OpenAICompatibleEmbedding(Embedding):
    """Embedding provider using any OpenAI-compatible embeddings API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package required for OpenAI-compatible embeddings. "
                "Install with: pip install openai"
            )

        self._model = model or os.environ.get("EMBED_MODEL", "text-embedding-3-small")
        self._api_key = api_key or os.environ.get("EMBED_API_KEY", "")
        self._base_url = base_url or os.environ.get("EMBED_BASE_URL", "https://api.openai.com/v1")
        self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        self._dimension: int | None = None

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self._model, input=[text])
        vector = response.data[0].embedding
        if self._dimension is None:
            self._dimension = len(vector)
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_CHUNK_SIZE):
            chunk = texts[i : i + _BATCH_CHUNK_SIZE]
            response = self._client.embeddings.create(model=self._model, input=chunk)
            embeddings = [d.embedding for d in response.data]
            results.extend(embeddings)
            if self._dimension is None and embeddings:
                self._dimension = len(embeddings[0])
        return results

    def get_dimension(self) -> int:
        if self._dimension is None:
            self.embed("dimension probe")
        return self._dimension

    def get_provider(self) -> str:
        return f"openai-compat/{self._model}"

    async def aembed(self, text: str) -> list[float]:
        """Async embed a single text string."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        response = await client.embeddings.create(model=self._model, input=[text])
        vector = response.data[0].embedding
        if self._dimension is None:
            self._dimension = len(vector)
        return vector

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        """Async embed multiple texts, chunked."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_CHUNK_SIZE):
            chunk = texts[i : i + _BATCH_CHUNK_SIZE]
            response = await client.embeddings.create(model=self._model, input=chunk)
            embeddings = [d.embedding for d in response.data]
            results.extend(embeddings)
            if self._dimension is None and embeddings:
                self._dimension = len(embeddings[0])
        return results
