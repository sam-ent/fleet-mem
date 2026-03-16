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

    # Ollama
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    ollama_embed_model: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    )

    # SQLite memory DB
    memory_db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MEMORY_DB_PATH", str(_DEFAULT_DIR / "memory.db"))
        )
    )

    # Fleet SQLite DB
    fleet_db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("FLEET_DB_PATH", str(_DEFAULT_DIR / "fleet.db"))
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
