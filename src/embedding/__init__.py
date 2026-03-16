"""Embedding provider abstractions."""

from src.embedding.base import Embedding
from src.embedding.cache import CachedEmbedding, EmbeddingCache
from src.embedding.ollama_embed import OllamaEmbedding
from src.embedding.openai_compat import OpenAICompatibleEmbedding

__all__ = [
    "CachedEmbedding",
    "Embedding",
    "EmbeddingCache",
    "OllamaEmbedding",
    "OpenAICompatibleEmbedding",
]
