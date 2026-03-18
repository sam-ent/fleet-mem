"""Fleet statistics for observability."""

import json
import sqlite3
from pathlib import Path


def get_fleet_stats(
    chroma_path: Path,
    memory_db_path: Path,
    fleet_db_path: Path,
    embed_cache_path: Path,
    detail: bool = False,
) -> dict:
    """Collect current fleet metrics.

    When *detail* is True, include individual lock, subscription, and
    notification rows (for the TUI monitor). Otherwise return counts only.
    """
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
        conn.row_factory = sqlite3.Row
        stats["active_locks"] = conn.execute("SELECT COUNT(*) FROM agent_locks").fetchone()[0]
        stats["subscriptions"] = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
        stats["pending_notifications"] = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
        ).fetchone()[0]

        if detail:
            # Individual lock rows
            lock_rows = conn.execute(
                "SELECT * FROM agent_locks WHERE status = 'active' ORDER BY acquired_at DESC"
            ).fetchall()
            stats["lock_details"] = [
                {
                    "id": r["id"],
                    "agent_id": r["agent_id"],
                    "project": r["project"],
                    "file_patterns": json.loads(r["file_patterns"]),
                    "branch": r["branch"],
                    "acquired_at": r["acquired_at"],
                    "expires_at": r["expires_at"],
                }
                for r in lock_rows
            ]

            # Individual subscription rows
            sub_rows = conn.execute(
                "SELECT * FROM subscriptions ORDER BY created_at DESC"
            ).fetchall()
            stats["subscription_details"] = [
                {
                    "id": r["id"],
                    "agent_id": r["agent_id"],
                    "project": r["project"],
                    "file_pattern": r["file_pattern"],
                    "created_at": r["created_at"],
                }
                for r in sub_rows
            ]

            # Recent notifications (last 50)
            notif_rows = conn.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            stats["notification_details"] = [
                {
                    "id": r["id"],
                    "subscriber_agent_id": r["subscriber_agent_id"],
                    "memory_id": r["memory_id"],
                    "memory_summary": r["memory_summary"],
                    "file_path": r["file_path"],
                    "author_agent_id": r["author_agent_id"],
                    "created_at": r["created_at"],
                    "read": r["read_at"] is not None,
                }
                for r in notif_rows
            ]

        conn.close()
    except Exception:
        stats["active_locks"] = 0
        stats["subscriptions"] = 0
        stats["pending_notifications"] = 0
        if detail:
            stats["lock_details"] = []
            stats["subscription_details"] = []
            stats["notification_details"] = []

    # Agent session stats
    try:
        conn = sqlite3.connect(str(fleet_db_path))
        conn.row_factory = sqlite3.Row
        # Ensure table exists (may not if sessions module hasn't been used yet)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_sessions ("
            "agent_id TEXT PRIMARY KEY, project TEXT NOT NULL, "
            "worktree_path TEXT, branch TEXT, "
            "connected_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'active')"
        )
        stats["active_agents"] = conn.execute(
            "SELECT COUNT(*) FROM agent_sessions WHERE status = 'active'"
        ).fetchone()[0]

        if detail:
            from fleet_mem.fleet.sessions import list_agents

            stats["agent_details"] = list_agents(fleet_db_path)

        conn.close()
    except Exception:
        stats["active_agents"] = 0
        if detail:
            stats["agent_details"] = []

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
