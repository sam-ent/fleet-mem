"""Embedding provider abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported only for type-checkers; not required at runtime so providers
    # without the ``tokenizer-aware`` extra installed keep working.
    from tokenizers import Tokenizer


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

    def get_tokenizer(self) -> "Tokenizer | None | Any":
        """Return a HuggingFace ``tokenizers.Tokenizer`` for the model, or None.

        Used by the indexer's token-aware chunk cap (issue #42). The default
        implementation returns ``None``, which causes the indexer to fall
        back to the char-based cap. Providers that can map the active model
        to a known tokenizer should override this and lazy-load the
        tokenizer (returning ``None`` on any import or load failure so the
        char-cap path stays available).

        The return type is loosely typed because the ``tokenizers`` library
        is an OPTIONAL dependency; callers should treat the result as
        opaque and only use ``.encode(text).ids`` to count tokens.
        """
        return None

    async def aembed(self, text: str) -> list[float]:
        """Async embed. Default falls back to sync."""
        return self.embed(text)

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        """Async batch embed. Default falls back to sync."""
        return self.embed_batch(texts)
