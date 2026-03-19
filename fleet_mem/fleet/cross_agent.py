"""Cross-agent memory sharing: feed, subscriptions, notifications."""

from __future__ import annotations

import datetime
import fnmatch
import sqlite3
import uuid
from pathlib import Path

from opentelemetry.trace import StatusCode

from fleet_mem.observability import get_tracer, hash_content

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    project TEXT NOT NULL,
    file_pattern TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    subscriber_agent_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    memory_summary TEXT NOT NULL,
    file_path TEXT NOT NULL,
    author_agent_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT
);
"""


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_CREATE_TABLES)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_project ON subscriptions(project)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifs_agent_unread "
        "ON notifications(subscriber_agent_id, read_at)"
    )
    conn.commit()
    return conn


def memory_feed(
    memory_db_path: Path,
    project_path: str | None = None,
    since_minutes: int = 60,
    agent_id: str | None = None,
) -> list[dict]:
    """Query memory_nodes for recent entries from OTHER agents."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.memory.feed") as span:
        if agent_id:
            span.set_attribute("fleet.agent_id", agent_id)
        span.set_attribute("fleet.since_minutes", since_minutes)
        try:
            conn = sqlite3.connect(str(memory_db_path))
            conn.row_factory = sqlite3.Row
            try:
                cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    minutes=since_minutes
                )
                cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

                query = "SELECT * FROM memory_nodes WHERE created_at >= ? AND archived = 0"
                params: list = [cutoff_str]

                if agent_id:
                    query += " AND (agent_id IS NULL OR agent_id != ?)"
                    params.append(agent_id)

                if project_path:
                    query += " AND project_path = ?"
                    params.append(project_path)

                query += " ORDER BY created_at DESC"

                rows = conn.execute(query, params).fetchall()
                results = [
                    {
                        "id": r["id"],
                        "node_type": r["node_type"],
                        "summary": r["summary"],
                        "agent_id": r["agent_id"],
                        "file_path": r["file_path"],
                        "created_at": r["created_at"],
                    }
                    for r in rows
                ]
                span.set_attribute("fleet.result_count", len(results))
                return results
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def memory_subscribe(
    fleet_db_path: Path,
    agent_id: str,
    project: str,
    file_patterns: list[str],
) -> list[dict]:
    """Insert subscription rows. Idempotent on exact duplicates."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.memory.subscribe") as span:
        span.set_attribute("fleet.agent_id", agent_id)
        span.set_attribute("fleet.project", project)
        span.set_attribute("fleet.file_patterns", hash_content(",".join(file_patterns)))
        try:
            conn = _connect(fleet_db_path)
            try:
                created = []
                for pattern in file_patterns:
                    existing = conn.execute(
                        "SELECT id FROM subscriptions "
                        "WHERE agent_id = ? AND project = ? AND file_pattern = ?",
                        (agent_id, project, pattern),
                    ).fetchone()
                    if existing:
                        continue
                    sub_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO subscriptions "
                        "(id, agent_id, project, file_pattern, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (sub_id, agent_id, project, pattern, _now_iso()),
                    )
                    created.append(
                        {
                            "id": sub_id,
                            "agent_id": agent_id,
                            "project": project,
                            "file_pattern": pattern,
                        }
                    )
                conn.commit()
                span.set_attribute("fleet.subscription_count", len(created))
                return created
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def memory_notifications(fleet_db_path: Path, agent_id: str) -> list[dict]:
    """Return unread notifications for agent, mark them as read."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.memory.notifications") as span:
        span.set_attribute("fleet.agent_id", agent_id)
        try:
            conn = _connect(fleet_db_path)
            try:
                rows = conn.execute(
                    "SELECT * FROM notifications WHERE subscriber_agent_id = ? AND read_at IS NULL "
                    "ORDER BY created_at DESC",
                    (agent_id,),
                ).fetchall()

                results = [
                    {
                        "id": r["id"],
                        "memory_id": r["memory_id"],
                        "memory_summary": r["memory_summary"],
                        "file_path": r["file_path"],
                        "author_agent_id": r["author_agent_id"],
                        "created_at": r["created_at"],
                    }
                    for r in rows
                ]

                if results:
                    now = _now_iso()
                    ids = [r["id"] for r in results]
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE notifications SET read_at = ? WHERE id IN ({placeholders})",  # noqa: S608
                        [now, *ids],
                    )
                    conn.commit()

                span.set_attribute("fleet.notification_count", len(results))
                return results
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def _notify_subscribers(
    fleet_db_path: Path,
    memory_id: str,
    memory_summary: str,
    file_path: str,
    author_agent_id: str,
) -> int:
    """Check subscriptions and create notifications for matching agents.

    Returns the number of notifications created.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.memory.notify_subscribers") as span:
        span.set_attribute("fleet.author_agent_id", author_agent_id)
        span.set_attribute("fleet.file_path_hash", hash_content(file_path))
        try:
            conn = _connect(fleet_db_path)
            try:
                subs = conn.execute("SELECT * FROM subscriptions").fetchall()
                now = _now_iso()
                created = 0
                for sub in subs:
                    if sub["agent_id"] == author_agent_id:
                        continue
                    if fnmatch.fnmatch(file_path, sub["file_pattern"]):
                        notif_id = str(uuid.uuid4())
                        conn.execute(
                            "INSERT INTO notifications "
                            "(id, subscriber_agent_id, memory_id, memory_summary, file_path, "
                            "author_agent_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                notif_id,
                                sub["agent_id"],
                                memory_id,
                                memory_summary,
                                file_path,
                                author_agent_id,
                                now,
                            ),
                        )
                        created += 1
                conn.commit()
                span.set_attribute("fleet.subscriber_count", len(subs))
                span.set_attribute("fleet.notification_count", created)
                return created
            finally:
                conn.close()
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
