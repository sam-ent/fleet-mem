"""Fleet statistics for observability."""

import sqlite3
from pathlib import Path


def get_fleet_stats(
    chroma_path: Path,
    memory_db_path: Path,
    fleet_db_path: Path,
    embed_cache_path: Path,
) -> dict:
    """Collect current fleet metrics."""
    stats: dict = {}

    # ChromaDB collection stats
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(chroma_path))
        collections = client.list_collections()
        stats["collections"] = {}
        for col in collections:
            c = client.get_collection(col.name)
            stats["collections"][col.name] = c.count()
        stats["total_chunks"] = sum(stats["collections"].values())
    except Exception:
        stats["collections"] = {}
        stats["total_chunks"] = 0

    # Memory stats
    try:
        conn = sqlite3.connect(str(memory_db_path))
        stats["memory_nodes"] = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        stats["file_anchors"] = conn.execute("SELECT COUNT(*) FROM file_anchors").fetchone()[0]
        conn.close()
    except Exception:
        stats["memory_nodes"] = 0
        stats["file_anchors"] = 0

    # Fleet stats (locks, subscriptions, notifications)
    try:
        conn = sqlite3.connect(str(fleet_db_path))
        stats["active_locks"] = conn.execute("SELECT COUNT(*) FROM agent_locks").fetchone()[0]
        stats["subscriptions"] = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
        stats["pending_notifications"] = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
        ).fetchone()[0]
        conn.close()
    except Exception:
        stats["active_locks"] = 0
        stats["subscriptions"] = 0
        stats["pending_notifications"] = 0

    # Embedding cache stats
    try:
        conn = sqlite3.connect(str(embed_cache_path))
        stats["cached_embeddings"] = conn.execute(
            "SELECT COUNT(*) FROM embedding_cache"
        ).fetchone()[0]
        conn.close()
    except Exception:
        stats["cached_embeddings"] = 0

    return stats
