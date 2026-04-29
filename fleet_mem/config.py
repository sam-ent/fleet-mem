"""Configuration from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_XDG_DATA = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
_DEFAULT_DIR = _XDG_DATA / "fleet-mem"


@dataclass
class Config:
    """Fleet-Mem server configuration."""

    # ChromaDB
    chroma_path: Path = field(
        default_factory=lambda: Path(os.environ.get("CHROMA_PATH", str(_DEFAULT_DIR / "chroma")))
    )

    # Embedding provider: "ollama" or "openai-compat"
    embedding_provider: str = field(
        default_factory=lambda: os.environ.get("EMBEDDING_PROVIDER", "ollama")
    )

    # Ollama
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    ollama_embed_model: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    )

    # OpenAI-compatible embedding provider
    embed_api_key: str = field(default_factory=lambda: os.environ.get("EMBED_API_KEY", ""))
    embed_base_url: str = field(
        default_factory=lambda: os.environ.get("EMBED_BASE_URL", "https://api.openai.com/v1")
    )
    embed_model: str = field(default_factory=lambda: os.environ.get("EMBED_MODEL", ""))

    # SQLite memory DB
    memory_db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MEMORY_DB_PATH", str(_DEFAULT_DIR / "memory.db"))
        )
    )

    # Embedding cache
    embed_cache_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("EMBED_CACHE_PATH", str(_DEFAULT_DIR / "embeddings_cache.db"))
        )
    )

    # Fleet SQLite DB
    fleet_db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("FLEET_DB_PATH", str(_DEFAULT_DIR / "fleet.db"))
        )
    )

    # Stats socket (Unix domain socket for monitoring TUI)
    stats_sock: str = field(default_factory=lambda: os.environ.get("FLEET_STATS_SOCK", ""))

    # OpenTelemetry
    otel_enabled: bool = field(
        default_factory=lambda: (
            os.environ.get("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")
        )
    )

    # File watching
    file_watching: bool = field(
        default_factory=lambda: (
            os.environ.get("FILE_WATCHING", "true").lower() in ("true", "1", "yes")
        )
    )

    # Chunk size cap (characters) applied before embedding so chunks fit
    # within the embed model's context window. Default 5000 chars is
    # approximately 1250 tokens for typical English text, leaving
    # comfortable headroom below a 2048-token model like nomic-embed-text.
    max_chunk_chars: int = field(
        default_factory=lambda: int(os.environ.get("FLEET_MEM_MAX_CHUNK_CHARS", "5000"))
    )

    # Optional token-aware cap (issue #42). When set AND the embed model's
    # tokenizer is loadable (via the `tokenizer-aware` extra), the chunker
    # subdivides chunks until each fits within ``max_chunk_tokens`` tokens
    # for the model's tokenizer — a stricter, content-aware bound than the
    # char-cap. Defaults to ``None`` (disabled), preserving existing behavior:
    # users on the char-cap path are unaffected.
    #
    # Recommended values: ~80% of the model's max position embeddings.
    # Examples: ``all-minilm`` (512 tok) -> 400; ``nomic-embed-text``
    # (2048 tok) -> 1600. If both caps are set, the token cap is applied
    # alongside the char cap (each chunk must satisfy both).
    max_chunk_tokens: int | None = field(
        default_factory=lambda: (
            int(os.environ["FLEET_MEM_MAX_CHUNK_TOKENS"])
            if os.environ.get("FLEET_MEM_MAX_CHUNK_TOKENS")
            else None
        )
    )

    # Merkle sync
    merkle_path: Path = field(default=None)
    sync_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("SYNC_INTERVAL", "300"))
    )

    def __post_init__(self):
        if self.merkle_path is None:
            self.merkle_path = self.chroma_path / "merkle"

        # Disable ChromaDB telemetry
        os.environ["ANONYMIZED_TELEMETRY"] = "False"

        # Ensure directories exist
        self.chroma_path.mkdir(parents=True, exist_ok=True)
        self.merkle_path.mkdir(parents=True, exist_ok=True)
