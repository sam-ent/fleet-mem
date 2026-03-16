"""Embedding provider abstract base class."""

from abc import ABC, abstractmethod


class Embedding(ABC):
    """Abstract embedding provider."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Embed a single text string."""

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts. Implementations should handle chunking."""

    @abstractmethod
    def get_dimension(self) -> int:
        """Return the embedding dimension."""

    @abstractmethod
    def get_provider(self) -> str:
        """Return the provider name (e.g. 'ollama/nomic-embed-text')."""
