#!/usr/bin/env python3
"""Import Claude memory flat files into agent_memory.db + ChromaDB.

Walks ~/.claude/projects/*/memory/*.md, parses YAML frontmatter,
and inserts each file as a memory_node with source='flat-file-import'.
Deduplicates by SHA-1 content hash.
"""

import hashlib
import sys
from pathlib import Path

# Allow running from repo root: add parent to sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.config import Config  # noqa: E402
from src.embedding.ollama_embed import OllamaEmbedding  # noqa: E402
from src.memory.embedder import MemoryEmbedder  # noqa: E402
from src.memory.engine import MemoryEngine  # noqa: E402
from src.vectordb.chromadb_store import ChromaDBStore  # noqa: E402


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter between --- markers. Returns (metadata, body)."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    # Simple key: value parsing (no full YAML dependency)
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value:
                meta[key] = value

    body = parts[2].strip()
    return meta, body


def _content_hash(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def _find_memory_files() -> list[Path]:
    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        return []
    return sorted(base.glob("*/memory/*.md"))


def _existing_hashes(engine: MemoryEngine) -> set[str]:
    """Get content hashes of all existing flat-file-import nodes."""
    cur = engine.conn.execute("SELECT content FROM memory_nodes WHERE source = 'flat-file-import'")
    return {_content_hash(row[0]) for row in cur.fetchall()}


def main() -> None:
    files = _find_memory_files()
    print(f"Found {len(files)} memory file(s)")

    if not files:
        return

    config = Config()
    engine = MemoryEngine(config.memory_db_path)
    engine.open()
    db = ChromaDBStore(config.chroma_path)
    embedding = OllamaEmbedding(config)
    embedder = MemoryEmbedder(engine, embedding, db)

    existing = _existing_hashes(engine)

    imported = 0
    skipped = 0

    for fp in files:
        text = fp.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)

        # Use body if frontmatter exists, otherwise full text
        content = body if body else text
        chash = _content_hash(content)

        if chash in existing:
            skipped += 1
            print(f"  SKIP (dup): {fp.name}")
            continue

        node_type = meta.get("type", "memory")
        summary = meta.get("description") or meta.get("name")

        embedder.memory_store(
            node_type=node_type,
            content=content,
            summary=summary,
            source="flat-file-import",
            file_path=str(fp),
        )
        existing.add(chash)
        imported += 1
        print(f"  IMPORTED:   {fp.name}")

    engine.close()
    print(f"\nDone: {len(files)} found, {imported} imported, {skipped} skipped (duplicates)")


if __name__ == "__main__":
    main()
