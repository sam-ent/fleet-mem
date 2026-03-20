"""Merge impact preview and post-merge notification."""

from __future__ import annotations

import fnmatch
import sqlite3
import uuid
from pathlib import Path

import structlog
from opentelemetry.trace import StatusCode

from fleet_mem.fleet.cross_agent import _notify_subscribers
from fleet_mem.fleet.lock_registry import lock_query
from fleet_mem.observability import get_tracer

logger = structlog.get_logger(__name__)


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def merge_impact(
    project: str,
    files: list[str],
    fleet_db_path: Path,
    memory_db_path: Path,
    chroma_path: Path | None = None,
) -> dict:
    """Read-only preview of what a merge touching *files* would affect.

    Returns a dict with locked_agents, subscribed_agents, stale_overlays,
    and stale_memories.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.merge.impact") as span:
        span.set_attribute("fleet.project", project)
        span.set_attribute("fleet.file_count", len(files))
        try:
            # --- locked agents ---
            locked_agents: list[dict] = []
            seen_agents: set[str] = set()
            for f in files:
                result = lock_query(fleet_db_path, project, file_path=f)
                for lock in result["locks"]:
                    if lock["agent_id"] not in seen_agents:
                        seen_agents.add(lock["agent_id"])
                        locked_agents.append(
                            {
                                "agent_id": lock["agent_id"],
                                "lock_id": lock["id"],
                                "file_patterns": lock["file_patterns"],
                            }
                        )

            # --- subscribed agents ---
            subscribed_agents: list[dict] = []
            seen_subs: set[str] = set()
            conn = sqlite3.connect(str(fleet_db_path))
            conn.row_factory = sqlite3.Row
            # Ensure tables exist
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS subscriptions ("
                "id TEXT PRIMARY KEY, agent_id TEXT, project TEXT, "
                "file_pattern TEXT, created_at TEXT);"
            )
            subs = conn.execute(
                "SELECT * FROM subscriptions WHERE project = ?", (project,)
            ).fetchall()
            for sub in subs:
                for f in files:
                    if fnmatch.fnmatch(f, sub["file_pattern"]):
                        if sub["agent_id"] not in seen_subs:
                            seen_subs.add(sub["agent_id"])
                            subscribed_agents.append(
                                {
                                    "agent_id": sub["agent_id"],
                                    "file_pattern": sub["file_pattern"],
                                }
                            )
                        break
            conn.close()

            # --- stale overlays ---
            stale_overlays: list[str] = []
            if chroma_path and chroma_path.exists():
                try:
                    import chromadb

                    client = chromadb.PersistentClient(path=str(chroma_path))
                    prefix = f"code_{project}__"
                    for col in client.list_collections():
                        name = col.name if hasattr(col, "name") else str(col)
                        if name.startswith(prefix):
                            c = client.get_collection(name)
                            # Check if any chunks reference affected files
                            for f in files:
                                results = c.get(where={"file_path": f}, limit=1)
                                if results["ids"]:
                                    stale_overlays.append(name)
                                    break
                except Exception:
                    pass

            # --- stale memories ---
            stale_memories: list[dict] = []
            if memory_db_path.exists():
                mem_conn = sqlite3.connect(str(memory_db_path))
                mem_conn.row_factory = sqlite3.Row
                try:
                    file_set = set(files)
                    rows = mem_conn.execute(
                        "SELECT fa.*, mn.project_path FROM file_anchors fa "
                        "JOIN memory_nodes mn ON fa.memory_id = mn.id "
                        "WHERE mn.archived = 0"
                    ).fetchall()
                    for row in rows:
                        if row["file_path"] in file_set:
                            stale_memories.append(
                                {
                                    "memory_id": row["memory_id"],
                                    "anchor_id": row["id"],
                                    "file_path": row["file_path"],
                                }
                            )
                except sqlite3.OperationalError:
                    pass
                finally:
                    mem_conn.close()

            span.set_attribute("fleet.conflict_count", len(locked_agents))
            span.set_attribute("fleet.subscriber_count", len(subscribed_agents))
            span.set_attribute("fleet.stale_anchor_count", len(stale_memories))

            return {
                "locked_agents": locked_agents,
                "subscribed_agents": subscribed_agents,
                "stale_overlays": stale_overlays,
                "stale_memories": stale_memories,
            }
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def notify_merge(
    project: str,
    branch: str,
    merged_files: list[str],
    fleet_db_path: Path,
    memory_db_path: Path,
) -> dict:
    """Post-merge: notify subscribed agents and identify stale file anchors.

    Returns a dict with notifications_created count and stale_anchors list.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.merge.notify") as span:
        span.set_attribute("fleet.project", project)
        span.set_attribute("fleet.branch", branch)
        span.set_attribute("fleet.file_count", len(merged_files))
        try:
            # Notify subscribers for each merged file
            notifications_created = 0
            for f in merged_files:
                merge_memory_id = f"merge-{branch}-{uuid.uuid4().hex[:8]}"
                created = _notify_subscribers(
                    fleet_db_path=fleet_db_path,
                    memory_id=merge_memory_id,
                    memory_summary=f"Branch '{branch}' merged, file changed: {f}",
                    file_path=f,
                    author_agent_id=f"merge:{branch}",
                    project=project,
                )
                notifications_created += created

            # Identify and mark stale file anchors
            stale_anchors: list[dict] = []
            if memory_db_path.exists():
                mem_conn = sqlite3.connect(str(memory_db_path))
                mem_conn.row_factory = sqlite3.Row
                try:
                    file_set = set(merged_files)
                    rows = mem_conn.execute(
                        "SELECT fa.* FROM file_anchors fa "
                        "JOIN memory_nodes mn ON fa.memory_id = mn.id "
                        "WHERE mn.archived = 0"
                    ).fetchall()
                    stale_ids = []
                    for row in rows:
                        if row["file_path"] in file_set:
                            stale_anchors.append(
                                {
                                    "memory_id": row["memory_id"],
                                    "anchor_id": row["id"],
                                    "file_path": row["file_path"],
                                }
                            )
                            stale_ids.append(row["id"])
                    # Persist staleness
                    if stale_ids:
                        placeholders = ",".join("?" for _ in stale_ids)
                        mem_conn.execute(
                            f"UPDATE file_anchors SET is_stale = 1 "  # noqa: S608
                            f"WHERE id IN ({placeholders})",
                            stale_ids,
                        )
                        mem_conn.commit()
                        logger.info(
                            "Marked %d file anchor(s) as stale",
                            len(stale_ids),
                        )
                except sqlite3.OperationalError:
                    pass
                finally:
                    mem_conn.close()

            # Release locks held on the merged branch
            released_count = 0
            try:
                conn = sqlite3.connect(str(fleet_db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "DELETE FROM agent_locks "
                    "WHERE project = ? AND branch = ? AND status = 'active'",
                    (project, branch),
                )
                released_count = cursor.rowcount
                conn.commit()
                conn.close()
                if released_count:
                    logger.info(
                        "Released %d lock(s) for merged branch %s",
                        released_count,
                        branch,
                    )
            except Exception:
                pass

            span.set_attribute("fleet.notification_count", notifications_created)
            span.set_attribute("fleet.stale_anchor_count", len(stale_anchors))
            span.set_attribute("fleet.released_locks", released_count)

            return {
                "notifications_created": notifications_created,
                "stale_anchors": stale_anchors,
                "released_locks": released_count,
            }
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
