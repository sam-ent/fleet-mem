"""Embedding provider abstractions."""

from src.embedding.base import Embedding
from src.embedding.ollama_embed import OllamaEmbedding

__all__ = ["Embedding", "OllamaEmbedding"]
