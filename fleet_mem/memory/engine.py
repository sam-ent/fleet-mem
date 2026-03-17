"""SQLite-backed memory storage engine."""

import sqlite3
from pathlib import Path

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    keywords TEXT,
    file_path TEXT,
    line_range TEXT,
    source TEXT NOT NULL DEFAULT 'agent',
    project_path TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS file_anchors (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memory_nodes(id),
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content,
    summary,
    content_rowid='rowid'
);
"""


class MemoryEngine:
    """Thin wrapper around SQLite for agent memory storage."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("MemoryEngine is not open. Use as context manager or call open().")
        return self._conn

    def _init_tables(self) -> None:
        cur = self.conn.executescript(_SCHEMA_SQL)
        cur.close()
        self._migrate_agent_id()

    def _migrate_agent_id(self) -> None:
        """Add agent_id column if it doesn't exist."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(memory_nodes)").fetchall()}
        if "agent_id" not in cols:
            self.conn.execute("ALTER TABLE memory_nodes ADD COLUMN agent_id TEXT")
            self.conn.commit()

    def insert_node(
        self,
        node_id: str,
        node_type: str,
        content: str,
        summary: str | None = None,
        keywords: str | None = None,
        file_path: str | None = None,
        line_range: str | None = None,
        source: str = "agent",
        project_path: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO memory_nodes
               (id, node_type, content, summary, keywords,
                file_path, line_range, source, project_path, agent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node_id,
                node_type,
                content,
                summary,
                keywords,
                file_path,
                line_range,
                source,
                project_path,
                agent_id,
            ),
        )
        # Insert into FTS index
        self.conn.execute(
            "INSERT INTO memory_fts (rowid, content, summary) VALUES (last_insert_rowid(), ?, ?)",
            (content, summary or ""),
        )
        self.conn.commit()

    def insert_file_anchor(
        self,
        anchor_id: str,
        memory_id: str,
        file_path: str,
        file_hash: str,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO file_anchors (id, memory_id, file_path, file_hash, line_start, line_end)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (anchor_id, memory_id, file_path, file_hash, line_start, line_end),
        )
        self.conn.commit()

    def get_node(self, node_id: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,))
        return cur.fetchone()

    def update_node_project_path(self, node_id: str, project_path: str | None) -> None:
        self.conn.execute(
            "UPDATE memory_nodes SET project_path = ?, updated_at = datetime('now') WHERE id = ?",
            (project_path, node_id),
        )
        self.conn.commit()

    def search_fts(self, query: str, limit: int = 10) -> list[sqlite3.Row]:
        safe_query = '"' + query.replace('"', '""') + '"'
        cur = self.conn.execute(
            """SELECT mn.* FROM memory_fts fts
               JOIN memory_nodes mn ON fts.rowid = mn.rowid
               WHERE memory_fts MATCH ?
               AND mn.archived = 0
               LIMIT ?""",
            (safe_query, limit),
        )
        return cur.fetchall()

    def get_all_file_anchors(self, project_path: str | None = None) -> list[sqlite3.Row]:
        if project_path:
            cur = self.conn.execute(
                """SELECT fa.*, mn.project_path FROM file_anchors fa
                   JOIN memory_nodes mn ON fa.memory_id = mn.id
                   WHERE mn.project_path = ? AND mn.archived = 0""",
                (project_path,),
            )
        else:
            cur = self.conn.execute(
                """SELECT fa.*, mn.project_path FROM file_anchors fa
                   JOIN memory_nodes mn ON fa.memory_id = mn.id
                   WHERE mn.archived = 0"""
            )
        return cur.fetchall()
