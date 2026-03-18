"""Agent session registry: tracks connected agents, worktrees, and activity."""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from opentelemetry.trace import StatusCode

from fleet_mem.observability import get_tracer

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    agent_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    worktree_path TEXT,
    branch TEXT,
    connected_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
)
"""

# Agents idle for this many minutes are marked disconnected
_IDLE_THRESHOLD_MINUTES = 5
# Sessions older than this are pruned
_STALE_THRESHOLD_HOURS = 24


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


def register_agent(
    db_path: Path,
    agent_id: str,
    project: str,
    worktree_path: str | None = None,
    branch: str | None = None,
) -> dict:
    """Register or update an agent session. Idempotent."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.agent.register") as span:
        span.set_attribute("fleet.agent_id", agent_id)
        span.set_attribute("fleet.project", project)
        if branch:
            span.set_attribute("fleet.branch", branch)
        try:
            conn = _connect(db_path)
            try:
                now = _iso(_now())
                existing = conn.execute(
                    "SELECT agent_id FROM agent_sessions WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()

                if existing:
                    conn.execute(
                        "UPDATE agent_sessions "
                        "SET project = ?, worktree_path = ?, branch = ?, "
                        "last_activity_at = ?, status = 'active' "
                        "WHERE agent_id = ?",
                        (project, worktree_path, branch, now, agent_id),
                    )
                else:
                    conn.execute(
                        "INSERT INTO agent_sessions "
                        "(agent_id, project, worktree_path, branch, "
                        "connected_at, last_activity_at, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                        (agent_id, project, worktree_path, branch, now, now),
                    )
                conn.commit()
                return {"agent_id": agent_id, "status": "registered"}
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def heartbeat_agent(db_path: Path, agent_id: str) -> None:
    """Update last_activity_at for an agent. No-op if agent not registered."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE agent_sessions SET last_activity_at = ?, status = 'active' WHERE agent_id = ?",
            (_iso(_now()), agent_id),
        )
        conn.commit()
    finally:
        conn.close()


def refresh_statuses(db_path: Path) -> None:
    """Mark idle/disconnected agents based on last_activity_at.

    Called by the stats endpoint before returning data.
    """
    conn = _connect(db_path)
    try:
        now = _now()
        idle_cutoff = _iso(now - datetime.timedelta(minutes=2))
        disc_cutoff = _iso(now - datetime.timedelta(minutes=_IDLE_THRESHOLD_MINUTES))
        stale_cutoff = _iso(now - datetime.timedelta(hours=_STALE_THRESHOLD_HOURS))

        # Prune stale sessions
        conn.execute(
            "DELETE FROM agent_sessions WHERE last_activity_at < ?",
            (stale_cutoff,),
        )

        # Mark disconnected (>5 min inactive)
        conn.execute(
            "UPDATE agent_sessions SET status = 'disconnected' "
            "WHERE last_activity_at < ? AND status != 'disconnected'",
            (disc_cutoff,),
        )

        # Mark idle (>2 min but <5 min inactive)
        conn.execute(
            "UPDATE agent_sessions SET status = 'idle' "
            "WHERE last_activity_at < ? AND last_activity_at >= ? "
            "AND status = 'active'",
            (idle_cutoff, disc_cutoff),
        )

        conn.commit()
    finally:
        conn.close()


def list_agents(db_path: Path) -> list[dict]:
    """Return all agent sessions with refreshed statuses."""
    refresh_statuses(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM agent_sessions ORDER BY last_activity_at DESC"
        ).fetchall()
        return [
            {
                "agent_id": r["agent_id"],
                "project": r["project"],
                "worktree_path": r["worktree_path"],
                "branch": r["branch"],
                "connected_at": r["connected_at"],
                "last_activity_at": r["last_activity_at"],
                "status": r["status"],
            }
            for r in rows
        ]
    finally:
        conn.close()
