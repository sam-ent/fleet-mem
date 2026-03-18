"""Agent lock/reservation registry backed by SQLite."""

from __future__ import annotations

import datetime
import fnmatch
import json
import sqlite3
import uuid
from pathlib import Path

from opentelemetry.trace import StatusCode

from fleet_mem.observability import get_tracer, hash_content

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS agent_locks (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    project TEXT NOT NULL,
    file_patterns TEXT NOT NULL,
    branch TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
)
"""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def _cleanup_expired(conn: sqlite3.Connection) -> None:
    """Delete locks whose expires_at is in the past."""
    conn.execute(
        "DELETE FROM agent_locks WHERE status = 'active' AND expires_at < ?",
        (_iso(_now()),),
    )
    conn.commit()


def _patterns_overlap(existing_patterns: list[str], new_patterns: list[str]) -> bool:
    """Check if any new pattern overlaps with any existing pattern via fnmatch."""
    for ep in existing_patterns:
        for np in new_patterns:
            # Check both directions: either could be more specific
            if fnmatch.fnmatch(np, ep) or fnmatch.fnmatch(ep, np):
                return True
    return False


def lock_acquire(
    db_path: Path,
    agent_id: str,
    project: str,
    file_patterns: list[str],
    branch: str,
    ttl_minutes: int = 60,
) -> dict:
    """Acquire a lock. Returns dict with status 'acquired' or 'conflict'."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.lock.acquire") as span:
        span.set_attribute("fleet.agent_id", agent_id)
        span.set_attribute("fleet.project", project)
        span.set_attribute("fleet.file_patterns", hash_content(json.dumps(file_patterns)))
        try:
            conn = _connect(db_path)
            try:
                _cleanup_expired(conn)

                # Find active locks for this project by OTHER agents
                rows = conn.execute(
                    "SELECT * FROM agent_locks "
                    "WHERE project = ? AND status = 'active' AND agent_id != ?",
                    (project, agent_id),
                ).fetchall()

                for row in rows:
                    existing = json.loads(row["file_patterns"])
                    if _patterns_overlap(existing, file_patterns):
                        span.set_attribute("fleet.conflict_count", 1)
                        span.set_attribute("fleet.lock.conflict_agent", row["agent_id"])
                        return {
                            "status": "conflict",
                            "conflicting_agent": row["agent_id"],
                            "conflicting_patterns": existing,
                            "conflicting_lock_id": row["id"],
                        }

                now = _now()
                lock_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO agent_locks "
                    "(id, agent_id, project, file_patterns, "
                    "branch, acquired_at, expires_at, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
                    (
                        lock_id,
                        agent_id,
                        project,
                        json.dumps(file_patterns),
                        branch,
                        _iso(now),
                        _iso(now + datetime.timedelta(minutes=ttl_minutes)),
                    ),
                )
                conn.commit()
                span.set_attribute("fleet.conflict_count", 0)
                span.set_attribute("fleet.lock_id", lock_id)
                return {"status": "acquired", "lock_id": lock_id}
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def lock_release(db_path: Path, agent_id: str, project: str) -> dict:
    """Release all locks for an agent on a project."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.lock.release") as span:
        span.set_attribute("fleet.agent_id", agent_id)
        span.set_attribute("fleet.project", project)
        try:
            conn = _connect(db_path)
            try:
                cursor = conn.execute(
                    "DELETE FROM agent_locks "
                    "WHERE agent_id = ? AND project = ? AND status = 'active'",
                    (agent_id, project),
                )
                conn.commit()
                span.set_attribute("fleet.released_count", cursor.rowcount)
                return {"status": "released", "count": cursor.rowcount}
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def lock_query(db_path: Path, project: str, file_path: str | None = None) -> dict:
    """List active locks, optionally filtered by file path overlap."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.lock.query") as span:
        span.set_attribute("fleet.project", project)
        try:
            conn = _connect(db_path)
            try:
                _cleanup_expired(conn)

                rows = conn.execute(
                    "SELECT * FROM agent_locks WHERE project = ? AND status = 'active'",
                    (project,),
                ).fetchall()

                locks = []
                for row in rows:
                    patterns = json.loads(row["file_patterns"])
                    if file_path is not None:
                        if not any(fnmatch.fnmatch(file_path, p) for p in patterns):
                            continue
                    locks.append(
                        {
                            "id": row["id"],
                            "agent_id": row["agent_id"],
                            "project": row["project"],
                            "file_patterns": patterns,
                            "branch": row["branch"],
                            "acquired_at": row["acquired_at"],
                            "expires_at": row["expires_at"],
                        }
                    )
                span.set_attribute("fleet.lock_count", len(locks))
                return {"locks": locks}
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def lock_heartbeat(db_path: Path, agent_id: str, ttl_minutes: int = 60) -> dict:
    """Extend expires_at on all active locks for an agent."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.lock.heartbeat") as span:
        span.set_attribute("fleet.agent_id", agent_id)
        try:
            conn = _connect(db_path)
            try:
                new_expires = _iso(_now() + datetime.timedelta(minutes=ttl_minutes))
                cursor = conn.execute(
                    "UPDATE agent_locks SET expires_at = ? "
                    "WHERE agent_id = ? AND status = 'active'",
                    (new_expires, agent_id),
                )
                conn.commit()
                span.set_attribute("fleet.extended_count", cursor.rowcount)
                return {
                    "status": "extended",
                    "count": cursor.rowcount,
                    "new_expires_at": new_expires,
                }
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
