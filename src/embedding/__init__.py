"""Embedding provider abstractions."""

from src.embedding.base import Embedding
from src.embedding.ollama_embed import OllamaEmbedding
from src.embedding.openai_compat import OpenAICompatibleEmbedding

__all__ = ["Embedding", "OllamaEmbedding", "OpenAICompatibleEmbedding"]
