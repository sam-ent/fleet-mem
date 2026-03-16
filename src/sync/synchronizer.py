"""File synchronizer: scans projects, manages snapshots."""

import json
from datetime import datetime, timezone
from pathlib import Path

import xxhash

from src.config import Config
from src.sync.merkle import MerkleDAG

# Default patterns to ignore (common non-source dirs/files)
DEFAULT_IGNORE = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".egg-info",
    ".tox",
    ".nox",
}


class FileSynchronizer:
    """Scans project files, computes hashes, saves/loads snapshots."""

    def __init__(self, config: Config, ignore_patterns: set[str] | None = None):
        self._config = config
        self._ignore = ignore_patterns if ignore_patterns is not None else DEFAULT_IGNORE

    def scan(self, project_path: Path) -> dict:
        """Walk project files, compute xxHash digests.

        Returns snapshot dict:
            {files: {relative_path: hash}, root_hash: str, timestamp: str}
        """
        dag = MerkleDAG()
        project_path = project_path.resolve()

        for file_path in sorted(project_path.rglob("*")):
            if not file_path.is_file():
                continue
            # Skip ignored directories
            rel = file_path.relative_to(project_path)
            if any(part in self._ignore for part in rel.parts):
                continue
            try:
                content = file_path.read_bytes()
            except (OSError, PermissionError):
                continue
            dag.add_node(str(rel), content)

        return {
            "files": dag.nodes,
            "root_hash": dag.root_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _snapshot_path(self, project_name: str) -> Path:
        return self._config.merkle_path / f"{project_name}.json"

    def save_snapshot(self, project_name: str, snapshot: dict) -> None:
        """Write snapshot JSON to merkle_path."""
        path = self._snapshot_path(project_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2))

    def load_snapshot(self, project_name: str) -> dict | None:
        """Read snapshot JSON. Returns None if not found."""
        path = self._snapshot_path(project_name)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    @staticmethod
    def file_hash(content: bytes) -> str:
        """xxh3_64 hex digest of content."""
        return xxhash.xxh3_64(content).hexdigest()
