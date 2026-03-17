"""Embedding provider abstractions."""

from fleet_mem.embedding.base import Embedding
from fleet_mem.embedding.cache import CachedEmbedding, EmbeddingCache
from fleet_mem.embedding.ollama_embed import OllamaEmbedding
from fleet_mem.embedding.openai_compat import OpenAICompatibleEmbedding

__all__ = [
    "CachedEmbedding",
    "Embedding",
    "EmbeddingCache",
    "OllamaEmbedding",
    "OpenAICompatibleEmbedding",
]
