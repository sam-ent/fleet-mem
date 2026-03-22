import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from fleet_mem.memory.engine import MemoryEngine


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_memory.db"


@pytest.fixture
def engine(db_path):
    engine = MemoryEngine(db_path)
    engine.open()
    yield engine
    engine.close()


def test_open_creates_directory_and_file(tmp_path):
    db_dir = tmp_path / "subdir"
    db_file = db_dir / "memory.db"
    engine = MemoryEngine(db_file)
    engine.open()
    assert db_dir.exists()
    assert db_file.exists()
    engine.close()


def test_conn_property_raises_if_closed(db_path):
    engine = MemoryEngine(db_path)
    with pytest.raises(RuntimeError, match="MemoryEngine is not open"):
        _ = engine.conn


def test_context_manager(db_path):
    with MemoryEngine(db_path) as engine:
        assert engine.conn is not None
        # Check if tables are initialized
        cur = engine.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row["name"] for row in cur.fetchall()}
        assert "memory_nodes" in tables
        assert "file_anchors" in tables
        assert "memory_fts" in tables


def test_insert_and_get_node(engine):
    node_id = "node-1"
    engine.insert_node(
        node_id=node_id,
        node_type="code_snippet",
        content="print('hello')",
        summary="hello world script",
        keywords="python,hello",
        file_path="hello.py",
        line_range="1-1",
        source="agent",
        project_path="/tmp/project",
        agent_id="agent-007",
    )

    node = engine.get_node(node_id)
    assert node is not None
    assert node["id"] == node_id
    assert node["content"] == "print('hello')"
    assert node["agent_id"] == "agent-007"
    assert node["project_path"] == "/tmp/project"


def test_update_node_project_path(engine):
    node_id = "node-1"
    engine.insert_node(node_id, "type", "content")

    engine.update_node_project_path(node_id, "/new/path")
    node = engine.get_node(node_id)
    assert node["project_path"] == "/new/path"

    engine.update_node_project_path(node_id, None)
    node = engine.get_node(node_id)
    assert node["project_path"] is None


def test_fts_search(engine):
    engine.insert_node("1", "type", "The quick brown fox", summary="fox summary")
    engine.insert_node("2", "type", "Jumps over the lazy dog", summary="dog summary")

    # Search for fox
    results = engine.search_fts("fox")
    assert len(results) == 1
    assert results[0]["id"] == "1"

    # Search for dog
    results = engine.search_fts("dog")
    assert len(results) == 1
    assert results[0]["id"] == "2"

    # Search for something non-existent
    results = engine.search_fts("cat")
    assert len(results) == 0


def test_fts_search_quotes_handling(engine):
    engine.insert_node("1", "type", 'A "quoted" string')
    results = engine.search_fts("quoted")
    assert len(results) == 1


def test_file_anchors(engine):
    engine.insert_node("node-1", "type", "content", project_path="/proj")
    engine.insert_file_anchor(
        anchor_id="anchor-1",
        memory_id="node-1",
        file_path="file.py",
        file_hash="abc",
        line_start=10,
        line_end=20,
    )

    anchors = engine.get_all_file_anchors()
    assert len(anchors) == 1
    assert anchors[0]["id"] == "anchor-1"
    assert anchors[0]["memory_id"] == "node-1"
    assert anchors[0]["project_path"] == "/proj"


def test_get_all_file_anchors_filtering(engine):
    engine.insert_node("n1", "type", "c1", project_path="/p1")
    engine.insert_node("n2", "type", "c2", project_path="/p2")

    engine.insert_file_anchor("a1", "n1", "f1.py", "h1")
    engine.insert_file_anchor("a2", "n2", "f2.py", "h2")

    p1_anchors = engine.get_all_file_anchors(project_path="/p1")
    assert len(p1_anchors) == 1
    assert p1_anchors[0]["id"] == "a1"

    all_anchors = engine.get_all_file_anchors()
    assert len(all_anchors) == 2


def test_migrations_on_existing_db(db_path):
    # Manually create a DB without the new columns
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE memory_nodes (id TEXT PRIMARY KEY, node_type TEXT,"
        " content TEXT, source TEXT DEFAULT 'agent')"
    )
    conn.execute(
        "CREATE TABLE file_anchors (id TEXT PRIMARY KEY, memory_id TEXT,"
        " file_path TEXT, file_hash TEXT)"
    )
    conn.close()

    # Opening with MemoryEngine should trigger migrations
    with MemoryEngine(db_path) as engine:
        cols_nodes = {
            row[1] for row in engine.conn.execute("PRAGMA table_info(memory_nodes)").fetchall()
        }
        assert "agent_id" in cols_nodes

        cols_anchors = {
            row[1] for row in engine.conn.execute("PRAGMA table_info(file_anchors)").fetchall()
        }
        assert "is_stale" in cols_anchors


def test_archived_nodes_excluded_from_search(engine):
    engine.insert_node("1", "type", "searchable content")
    engine.conn.execute("UPDATE memory_nodes SET archived = 1 WHERE id = '1'")
    engine.conn.commit()

    results = engine.search_fts("searchable")
    assert len(results) == 0


def test_archived_nodes_excluded_from_anchors(engine):
    engine.insert_node("1", "type", "content", project_path="/p1")
    engine.insert_file_anchor("a1", "1", "f1.py", "h1")

    engine.conn.execute("UPDATE memory_nodes SET archived = 1 WHERE id = '1'")
    engine.conn.commit()

    anchors = engine.get_all_file_anchors()
    assert len(anchors) == 0


@patch("sqlite3.connect")
def test_open_with_sqlite_params(mock_connect, db_path):
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn

    engine = MemoryEngine(db_path)
    engine.open()

    # Verify PRAGMAs were set
    calls = [call[0][0] for call in mock_conn.execute.call_args_list]
    assert "PRAGMA journal_mode=WAL" in calls
    assert "PRAGMA busy_timeout=5000" in calls
    assert "PRAGMA foreign_keys=ON" in calls
