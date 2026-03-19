"""Tests for fleet stats collector."""

import sqlite3
from pathlib import Path

from fleet_mem.fleet.stats import get_fleet_stats


def _create_memory_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE memory_nodes (id TEXT PRIMARY KEY, content TEXT, node_type TEXT)")
    conn.execute("CREATE TABLE file_anchors (id TEXT PRIMARY KEY, memory_id TEXT, file_path TEXT)")
    conn.execute("INSERT INTO memory_nodes VALUES ('n1', 'test', 'note')")
    conn.execute("INSERT INTO memory_nodes VALUES ('n2', 'test2', 'decision')")
    conn.execute("INSERT INTO file_anchors VALUES ('a1', 'n1', 'foo.py')")
    conn.commit()
    conn.close()


def _create_fleet_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE agent_locks (id TEXT PRIMARY KEY, agent_id TEXT, "
        "project TEXT, file_patterns TEXT, branch TEXT, acquired_at TEXT, "
        "expires_at TEXT, status TEXT DEFAULT 'active', UNIQUE(agent_id, project))"
    )
    conn.execute("CREATE TABLE subscriptions (id TEXT PRIMARY KEY, agent_id TEXT, pattern TEXT)")
    conn.execute("CREATE TABLE notifications (id TEXT PRIMARY KEY, agent_id TEXT, read_at TEXT)")
    conn.execute(
        "INSERT INTO agent_locks VALUES "
        "('l1', 'alpha', 'proj', '[]', 'main', '2026-01-01', '2099-01-01', 'active')"
    )
    conn.execute("INSERT INTO subscriptions VALUES ('s1', 'alpha', '*.py')")
    conn.execute("INSERT INTO notifications VALUES ('n1', 'alpha', NULL)")
    conn.execute("INSERT INTO notifications VALUES ('n2', 'alpha', '2024-01-01')")
    conn.commit()
    conn.close()


def _create_embed_cache_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE embedding_cache (content_hash TEXT PRIMARY KEY, vector BLOB)")
    conn.execute("INSERT INTO embedding_cache VALUES ('h1', X'00')")
    conn.execute("INSERT INTO embedding_cache VALUES ('h2', X'00')")
    conn.execute("INSERT INTO embedding_cache VALUES ('h3', X'00')")
    conn.commit()
    conn.close()


def test_get_fleet_stats_with_data(tmp_path):
    memory_db = tmp_path / "memory.db"
    fleet_db = tmp_path / "fleet.db"
    embed_cache = tmp_path / "embed.db"
    chroma_path = tmp_path / "chroma"
    chroma_path.mkdir()

    _create_memory_db(memory_db)
    _create_fleet_db(fleet_db)
    _create_embed_cache_db(embed_cache)

    stats = get_fleet_stats(chroma_path, memory_db, fleet_db, embed_cache)

    assert stats["memory_nodes"] == 2
    assert stats["file_anchors"] == 1
    assert stats["active_locks"] == 1
    assert stats["subscriptions"] == 1
    assert stats["pending_notifications"] == 1
    assert stats["cached_embeddings"] == 3
    assert stats["total_chunks"] == 0


def test_get_fleet_stats_missing_dbs(tmp_path):
    chroma_path = tmp_path / "chroma"
    chroma_path.mkdir()

    stats = get_fleet_stats(
        chroma_path,
        tmp_path / "nonexistent.db",
        tmp_path / "also_nonexistent.db",
        tmp_path / "nope.db",
    )

    assert stats["memory_nodes"] == 0
    assert stats["file_anchors"] == 0
    assert stats["active_locks"] == 0
    assert stats["subscriptions"] == 0
    assert stats["pending_notifications"] == 0
    assert stats["cached_embeddings"] == 0
