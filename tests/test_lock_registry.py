"""Tests for agent lock/reservation registry."""

import datetime
import json
import sqlite3

from src.fleet.lock_registry import (
    lock_acquire,
    lock_heartbeat,
    lock_query,
    lock_release,
)


def test_acquire_returns_acquired(tmp_path):
    db = tmp_path / "fleet.db"
    result = lock_acquire(db, "agent-a", "xfaci", ["src/auth/*"], "feat/auth")
    assert result["status"] == "acquired"
    assert "lock_id" in result


def test_acquire_conflicting_pattern_returns_conflict(tmp_path):
    db = tmp_path / "fleet.db"
    lock_acquire(db, "agent-a", "xfaci", ["src/auth/*"], "feat/auth")
    result = lock_acquire(db, "agent-b", "xfaci", ["src/auth/login.py"], "feat/login")
    assert result["status"] == "conflict"
    assert result["conflicting_agent"] == "agent-a"


def test_no_conflict_different_paths(tmp_path):
    db = tmp_path / "fleet.db"
    lock_acquire(db, "agent-a", "xfaci", ["src/auth/*"], "feat/auth")
    result = lock_acquire(db, "agent-b", "xfaci", ["src/api/*"], "feat/api")
    assert result["status"] == "acquired"


def test_no_conflict_same_agent(tmp_path):
    db = tmp_path / "fleet.db"
    lock_acquire(db, "agent-a", "xfaci", ["src/auth/*"], "feat/auth")
    result = lock_acquire(db, "agent-a", "xfaci", ["src/auth/login.py"], "feat/auth2")
    assert result["status"] == "acquired"


def test_release_then_reacquire(tmp_path):
    db = tmp_path / "fleet.db"
    lock_acquire(db, "agent-a", "xfaci", ["src/auth/*"], "feat/auth")
    rel = lock_release(db, "agent-a", "xfaci")
    assert rel["status"] == "released"
    assert rel["count"] == 1
    result = lock_acquire(db, "agent-b", "xfaci", ["src/auth/login.py"], "feat/login")
    assert result["status"] == "acquired"


def test_query_lists_active_locks(tmp_path):
    db = tmp_path / "fleet.db"
    lock_acquire(db, "agent-a", "xfaci", ["src/auth/*"], "feat/auth")
    lock_acquire(db, "agent-b", "xfaci", ["src/api/*"], "feat/api")
    result = lock_query(db, "xfaci")
    assert len(result["locks"]) == 2


def test_query_filters_by_file_path(tmp_path):
    db = tmp_path / "fleet.db"
    lock_acquire(db, "agent-a", "xfaci", ["src/auth/*"], "feat/auth")
    lock_acquire(db, "agent-b", "xfaci", ["src/api/*"], "feat/api")
    result = lock_query(db, "xfaci", file_path="src/auth/login.py")
    assert len(result["locks"]) == 1
    assert result["locks"][0]["agent_id"] == "agent-a"


def test_heartbeat_extends_ttl(tmp_path):
    db = tmp_path / "fleet.db"
    lock_acquire(db, "agent-a", "xfaci", ["src/auth/*"], "feat/auth", ttl_minutes=10)
    result = lock_heartbeat(db, "agent-a", ttl_minutes=120)
    assert result["status"] == "extended"
    assert result["count"] == 1
    # Verify the new expiry is further out
    locks = lock_query(db, "xfaci")
    expires = datetime.datetime.fromisoformat(locks["locks"][0]["expires_at"])
    now = datetime.datetime.now(datetime.timezone.utc)
    assert expires > now + datetime.timedelta(minutes=60)


def test_expired_locks_cleaned_up(tmp_path):
    db = tmp_path / "fleet.db"
    # Insert an already-expired lock directly
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_locks "
        "(id TEXT PRIMARY KEY, agent_id TEXT, project TEXT, file_patterns TEXT, "
        "branch TEXT, acquired_at TEXT, expires_at TEXT, status TEXT)"
    )
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
    conn.execute(
        "INSERT INTO agent_locks VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
        ("old-lock", "agent-a", "xfaci", json.dumps(["src/auth/*"]), "feat/old", past, past),
    )
    conn.commit()
    conn.close()

    # Expired lock should not cause conflict
    result = lock_acquire(db, "agent-b", "xfaci", ["src/auth/login.py"], "feat/login")
    assert result["status"] == "acquired"

    # Expired lock should not appear in query
    q = lock_query(db, "xfaci")
    agent_ids = [lock["agent_id"] for lock in q["locks"]]
    assert "agent-a" not in agent_ids
