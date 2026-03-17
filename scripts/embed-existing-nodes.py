#!/usr/bin/env python3
"""Embed existing memory_nodes into ChromaDB.

Reads all nodes from agent_memory.db and inserts any that are missing
from the ChromaDB memory collection. Skips nodes already embedded.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from fleet_mem.config import Config  # noqa: E402
from fleet_mem.embedding.ollama_embed import OllamaEmbedding  # noqa: E402
from fleet_mem.memory.embedder import MEMORY_COLLECTION  # noqa: E402
from fleet_mem.memory.engine import MemoryEngine  # noqa: E402
from fleet_mem.vectordb.chromadb_store import ChromaDBStore  # noqa: E402
from fleet_mem.vectordb.types import VectorDocument  # noqa: E402


def main() -> None:
    config = Config()
    engine = MemoryEngine(config.memory_db_path)
    engine.open()

    if not config.memory_db_path.exists():
        print("No agent_memory.db found. Nothing to embed.")
        engine.close()
        return

    db = ChromaDBStore(config.chroma_path)
    embedding = OllamaEmbedding(config)

    # Ensure memory collection exists
    if not db.has_collection(MEMORY_COLLECTION):
        dim = embedding.get_dimension()
        db.create_collection(MEMORY_COLLECTION, dimension=dim)

    col = db._client.get_collection(name=MEMORY_COLLECTION)

    # Get all nodes
    cur = engine.conn.execute(
        "SELECT id, node_type, content, source FROM memory_nodes WHERE archived = 0"
    )
    rows = cur.fetchall()
    print(f"Found {len(rows)} node(s) in agent_memory.db")

    # Get existing IDs in ChromaDB
    existing_ids: set[str] = set()
    if col.count() > 0:
        result = col.get(include=[])
        existing_ids = set(result["ids"])

    embedded = 0
    skipped = 0

    for row in rows:
        node_id = row[0]
        node_type = row[1]
        content = row[2]
        source = row[3]

        if node_id in existing_ids:
            skipped += 1
            continue

        vector = embedding.embed(content)
        doc = VectorDocument(
            id=node_id,
            content=content,
            metadata={"node_type": node_type, "source": source},
            vector=vector,
        )
        db.insert(MEMORY_COLLECTION, [doc])
        embedded += 1

    engine.close()
    print(f"Done: {len(rows)} total, {embedded} embedded, {skipped} skipped (already in ChromaDB)")


if __name__ == "__main__":
    main()
