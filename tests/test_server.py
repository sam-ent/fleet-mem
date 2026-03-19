"""Tests for MCP server tools — verify each tool returns correct response format."""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vec(seed: float = 1.0, dim: int = 8) -> list[float]:
    return [seed * (i + 1) * 0.1 for i in range(dim)]


@pytest.fixture(autouse=True)
def _reset_index_status():
    """Reset the global index status dict between tests."""
    from fleet_mem.server import _index_status

    _index_status.clear()
    yield
    _index_status.clear()


@pytest.fixture(autouse=True)
def _reset_bg_sync():
    """Reset the background sync flag between tests."""
    import fleet_mem.server

    fleet_mem.server._bg_syncs_started = False
    yield
    fleet_mem.server._bg_syncs_started = False


@pytest.fixture
def mock_config(tmp_path):
    cfg = MagicMock()
    cfg.chroma_path = tmp_path / "chroma"
    cfg.chroma_path.mkdir()
    cfg.merkle_path = tmp_path / "merkle"
    cfg.merkle_path.mkdir()
    cfg.ollama_host = "http://localhost:11434"
    cfg.ollama_embed_model = "nomic-embed-text"
    cfg.memory_db_path = tmp_path / "memory.db"
    return cfg


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.has_collection.return_value = False
    db.list_collections.return_value = []
    db.count.return_value = 0
    return db


@pytest.fixture
def mock_embedder():
    emb = MagicMock()
    emb.embed.return_value = _vec()
    emb.embed_batch.return_value = [_vec()]
    emb.aembed = AsyncMock(return_value=_vec())
    emb.aembed_batch = AsyncMock(return_value=[_vec()])
    emb.get_dimension.return_value = 8
    emb.cache_hits = 0
    emb.cache_misses = 0
    return emb


@pytest.fixture
def _patch_deps(mock_config, mock_db, mock_embedder):
    """Patch the server's factory functions."""
    with (
        patch("fleet_mem.server._get_config", return_value=mock_config),
        patch("fleet_mem.server._get_db", return_value=mock_db),
        patch("fleet_mem.server._get_embedder", return_value=mock_embedder),
        patch("fleet_mem.server._get_memory") as mock_mem_factory,
        patch("fleet_mem.server._ensure_background_sync", new_callable=AsyncMock),
    ):
        mock_mem = MagicMock()
        mock_mem_factory.return_value = mock_mem
        yield {
            "config": mock_config,
            "db": mock_db,
            "embedder": mock_embedder,
            "memory": mock_mem,
        }


# ---------------------------------------------------------------------------
# _repo_root_from_git / _project_name_from_path (worktree awareness)
# ---------------------------------------------------------------------------


def _git_init_with_commit(repo_path):
    """Initialise a git repo with one commit (works on CI without global config)."""
    subprocess.run(["git", "init", str(repo_path)], capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@test",
            "commit",
            "--allow-empty",
            "-m",
            "init",
        ],
        capture_output=True,
        cwd=str(repo_path),
        check=True,
    )


class TestRepoRootFromGit:
    def test_returns_repo_root_in_normal_repo(self, tmp_path):
        """In a standard git repo, returns the repo root."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
        from fleet_mem.server import _repo_root_from_git

        root = _repo_root_from_git(tmp_path)
        assert root == tmp_path

    def test_returns_main_root_from_worktree(self, tmp_path):
        """In a git worktree, returns the main repo root, not the worktree dir."""
        main = tmp_path / "main-repo"
        main.mkdir()
        _git_init_with_commit(main)
        wt = tmp_path / "my-worktree"
        subprocess.run(
            ["git", "worktree", "add", str(wt), "-b", "wt-branch"],
            capture_output=True,
            cwd=str(main),
            check=True,
        )
        from fleet_mem.server import _repo_root_from_git

        root = _repo_root_from_git(wt)
        assert root == main.resolve()

    def test_returns_none_for_non_git_dir(self, tmp_path):
        """Outside a git repo, returns None."""
        from fleet_mem.server import _repo_root_from_git

        root = _repo_root_from_git(tmp_path)
        assert root is None


class TestProjectNameFromPath:
    def test_non_git_directory_uses_basename(self, tmp_path):
        """Falls back to directory basename when not in a git repo."""
        target = tmp_path / "my-project"
        target.mkdir()
        from fleet_mem.server import _project_name_from_path

        assert _project_name_from_path(str(target)) == "my-project"

    def test_worktree_uses_main_repo_name(self, tmp_path):
        """Worktree path resolves to the main repo's name."""
        main = tmp_path / "fleet-mem"
        main.mkdir()
        _git_init_with_commit(main)
        wt = tmp_path / "fleet-mem-fix-foo"
        subprocess.run(
            ["git", "worktree", "add", str(wt), "-b", "fix-foo"],
            capture_output=True,
            cwd=str(main),
            check=True,
        )
        from fleet_mem.server import _project_name_from_path

        assert _project_name_from_path(str(wt)) == "fleet-mem"


# ---------------------------------------------------------------------------
# _auto_coordinate
# ---------------------------------------------------------------------------


class TestAutoCoordinate:
    def test_locks_and_subscribes_on_branch(self, tmp_path):
        """Auto-coordination acquires locks and subscriptions for modified files."""
        fleet_db = tmp_path / "fleet.db"
        cfg = MagicMock()
        cfg.fleet_db_path = fleet_db

        import fleet_mem.server

        old_id = fleet_mem.server._agent_id
        fleet_mem.server._agent_id = "test-agent-auto"
        try:
            # Mock subprocess.run inside _auto_coordinate to return modified files
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "app.py\nlib/utils.py\n"

            with patch("subprocess.run", return_value=mock_result):
                from fleet_mem.server import _auto_coordinate

                _auto_coordinate(cfg, "myrepo", "feat/test")
        finally:
            fleet_mem.server._agent_id = old_id

        # Verify lock was created
        import json
        import sqlite3

        conn = sqlite3.connect(str(fleet_db))
        conn.row_factory = sqlite3.Row
        locks = conn.execute("SELECT * FROM agent_locks WHERE status = 'active'").fetchall()
        assert len(locks) == 1
        assert locks[0]["agent_id"] == "test-agent-auto"
        assert json.loads(locks[0]["file_patterns"]) == ["app.py", "lib/utils.py"]
        assert locks[0]["branch"] == "feat/test"

        # Verify subscriptions were created
        subs = conn.execute("SELECT * FROM subscriptions ORDER BY file_pattern").fetchall()
        assert len(subs) == 2
        assert subs[0]["file_pattern"] == "app.py"
        assert subs[1]["file_pattern"] == "lib/utils.py"
        conn.close()

    def test_skips_on_main_branch(self, tmp_path):
        """No auto-coordination on main/master branches."""
        cfg = MagicMock()
        cfg.fleet_db_path = tmp_path / "fleet.db"

        mock_branch = MagicMock()
        mock_branch.returncode = 0
        mock_branch.stdout = "main\n"

        with (
            patch("fleet_mem.server._repo_root_from_git", return_value=tmp_path),
            patch("subprocess.run", return_value=mock_branch),
            patch("fleet_mem.server._auto_coordinate") as mock_auto,
        ):
            from fleet_mem.server import _register_agent

            _register_agent(cfg)

        mock_auto.assert_not_called()

    def test_no_modified_files_skips(self, tmp_path):
        """No locks/subs when branch has no diff from main."""
        fleet_db = tmp_path / "fleet.db"
        cfg = MagicMock()
        cfg.fleet_db_path = fleet_db

        import fleet_mem.server

        old_id = fleet_mem.server._agent_id
        fleet_mem.server._agent_id = "test-agent-empty"
        try:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""

            with patch("subprocess.run", return_value=mock_result):
                from fleet_mem.server import _auto_coordinate

                _auto_coordinate(cfg, "myrepo", "feat/no-changes")
        finally:
            fleet_mem.server._agent_id = old_id

        # No DB file should be created (no lock/sub calls)
        assert not fleet_db.exists()


# ---------------------------------------------------------------------------
# notify_merge lock release
# ---------------------------------------------------------------------------


class TestNotifyMergeReleasesLocks:
    def test_releases_locks_for_merged_branch(self, tmp_path):
        """notify_merge should release locks on the merged branch."""
        import json
        import sqlite3

        fleet_db = tmp_path / "fleet.db"
        memory_db = tmp_path / "memory.db"

        # Set up a lock
        conn = sqlite3.connect(str(fleet_db))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_locks ("
            "id TEXT PRIMARY KEY, agent_id TEXT, project TEXT, "
            "file_patterns TEXT, branch TEXT, acquired_at TEXT, "
            "expires_at TEXT, status TEXT DEFAULT 'active')"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS subscriptions ("
            "id TEXT PRIMARY KEY, agent_id TEXT, project TEXT, "
            "file_pattern TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notifications ("
            "id TEXT PRIMARY KEY, subscriber_agent_id TEXT, "
            "memory_id TEXT, memory_summary TEXT, file_path TEXT, "
            "author_agent_id TEXT, created_at TEXT, read_at TEXT)"
        )
        conn.execute(
            "INSERT INTO agent_locks VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
            (
                "lock1",
                "agent-abc",
                "myrepo",
                json.dumps(["app.py"]),
                "feat/test",
                "2026-01-01T00:00:00",
                "2026-12-31T00:00:00",
            ),
        )
        conn.commit()
        conn.close()

        from fleet_mem.fleet.merge_impact import notify_merge

        result = notify_merge(
            project="myrepo",
            branch="feat/test",
            merged_files=["app.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )

        assert result["released_locks"] == 1

        # Verify lock is gone
        conn = sqlite3.connect(str(fleet_db))
        remaining = conn.execute(
            "SELECT COUNT(*) FROM agent_locks WHERE status = 'active'"
        ).fetchone()[0]
        assert remaining == 0
        conn.close()


# ---------------------------------------------------------------------------
# Lock UPSERT deduplication
# ---------------------------------------------------------------------------


class TestLockUpsert:
    def test_same_agent_upserts_not_duplicates(self, tmp_path):
        """Acquiring a lock twice for same agent+project updates, not duplicates."""
        import json
        import sqlite3

        from fleet_mem.fleet.lock_registry import lock_acquire

        db = tmp_path / "fleet.db"
        r1 = lock_acquire(db, "agent-1", "proj", ["a.py"], "feat/x")
        assert r1["status"] == "acquired"
        r2 = lock_acquire(db, "agent-1", "proj", ["b.py"], "feat/y")
        assert r2["status"] == "acquired"

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM agent_locks WHERE agent_id = 'agent-1'").fetchall()
        assert len(rows) == 1  # one row, not two
        assert json.loads(rows[0]["file_patterns"]) == ["b.py"]
        assert rows[0]["branch"] == "feat/y"
        conn.close()

    def test_different_agents_still_conflict(self, tmp_path):
        """Two different agents on overlapping files still get conflict."""
        from fleet_mem.fleet.lock_registry import lock_acquire

        db = tmp_path / "fleet.db"
        r1 = lock_acquire(db, "agent-1", "proj", ["src/app.py"], "feat/x")
        assert r1["status"] == "acquired"
        r2 = lock_acquire(db, "agent-2", "proj", ["src/app.py"], "feat/y")
        assert r2["status"] == "conflict"
        assert r2["conflicting_agent"] == "agent-1"


class TestLockSchemaMigration:
    def test_migrates_old_schema(self, tmp_path):
        """Old schema without UNIQUE gets migrated, duplicates are deduped."""
        import json
        import sqlite3

        db = tmp_path / "fleet.db"
        conn = sqlite3.connect(str(db))
        # Create old schema (no UNIQUE constraint)
        conn.execute(
            "CREATE TABLE agent_locks ("
            "id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, "
            "project TEXT NOT NULL, file_patterns TEXT NOT NULL, "
            "branch TEXT NOT NULL, acquired_at TEXT NOT NULL, "
            "expires_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active')"
        )
        # Insert duplicates
        conn.execute(
            "INSERT INTO agent_locks VALUES "
            "('old1','agent-1','proj','[\"a.py\"]','b1','2026-01-01T00:00:00',"
            "'2099-01-01T00:00:00','active')"
        )
        conn.execute(
            "INSERT INTO agent_locks VALUES "
            "('old2','agent-1','proj','[\"b.py\"]','b2','2026-01-02T00:00:00',"
            "'2099-01-01T00:00:00','active')"
        )
        conn.commit()
        conn.close()

        # Opening a connection triggers migration
        from fleet_mem.fleet.lock_registry import _connect

        conn = _connect(db)
        rows = conn.execute("SELECT * FROM agent_locks WHERE agent_id = 'agent-1'").fetchall()
        assert len(rows) == 1
        # Should keep the most recent (acquired_at 2026-01-02)
        assert json.loads(rows[0]["file_patterns"]) == ["b.py"]

        # Verify UNIQUE constraint exists
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='agent_locks'").fetchone()[
            "sql"
        ]
        assert "UNIQUE" in schema
        conn.close()


# ---------------------------------------------------------------------------
# index_codebase
# ---------------------------------------------------------------------------


class TestIndexCodebase:
    @pytest.mark.asyncio
    async def test_returns_indexing_status(self, _patch_deps):
        from fleet_mem.server import index_codebase

        with patch("fleet_mem.server.threading") as mock_threading:
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread

            result = await index_codebase(path="/tmp/myproject")

        assert result["project"] == "myproject"
        assert result["status"] == "indexing"
        mock_thread.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_indexed_if_collection_exists(self, _patch_deps):
        from fleet_mem.server import index_codebase

        _patch_deps["db"].has_collection.return_value = True
        _patch_deps["db"].count.return_value = 42

        result = await index_codebase(path="/tmp/myproject", force=False)

        assert result["status"] == "indexed"
        assert result["chunk_count"] == 42

    @pytest.mark.asyncio
    async def test_force_reindexes(self, _patch_deps):
        from fleet_mem.server import index_codebase

        _patch_deps["db"].has_collection.return_value = True
        _patch_deps["db"].count.return_value = 42

        with patch("fleet_mem.server.threading") as mock_threading:
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread

            result = await index_codebase(path="/tmp/myproject", force=True)

        assert result["status"] == "indexing"


# ---------------------------------------------------------------------------
# search_code
# ---------------------------------------------------------------------------


class TestSearchCode:
    @pytest.mark.asyncio
    async def test_returns_results_structure(self, _patch_deps):
        from fleet_mem.server import search_code

        _patch_deps["db"].list_collections.return_value = ["code_myproject"]
        _patch_deps["db"].has_collection.return_value = True
        _patch_deps["db"].search.return_value = [
            {
                "id": "abc123",
                "content": "def hello(): pass",
                "score": 0.95,
                "metadata": {
                    "file_path": "src/main.py",
                    "start_line": 1,
                    "end_line": 2,
                    "project_name": "myproject",
                },
            }
        ]

        results = await search_code(query="hello function")

        assert len(results) == 1
        r = results[0]
        assert r["file_path"] == "src/main.py"
        assert r["start_line"] == 1
        assert r["end_line"] == 2
        assert r["snippet"] == "def hello(): pass"
        assert r["score"] == 0.95
        assert r["project"] == "myproject"

    @pytest.mark.asyncio
    async def test_scoped_to_project(self, _patch_deps):
        from fleet_mem.server import search_code

        _patch_deps["db"].has_collection.return_value = True
        _patch_deps["db"].search.return_value = []

        await search_code(query="test", path="/tmp/myproject")

        _patch_deps["db"].search.assert_called_once()

    @pytest.mark.asyncio
    async def test_extension_filter(self, _patch_deps):
        from fleet_mem.server import search_code

        _patch_deps["db"].list_collections.return_value = ["code_proj"]
        _patch_deps["db"].has_collection.return_value = True
        _patch_deps["db"].search.return_value = []

        await search_code(query="test", extension_filter="python")

        call_args = _patch_deps["db"].search.call_args
        assert call_args[1]["where"] == {"language": "python"}

    @pytest.mark.asyncio
    async def test_empty_collections(self, _patch_deps):
        from fleet_mem.server import search_code

        _patch_deps["db"].list_collections.return_value = []
        results = await search_code(query="test")
        assert results == []


# ---------------------------------------------------------------------------
# clear_index
# ---------------------------------------------------------------------------


class TestClearIndex:
    @pytest.mark.asyncio
    async def test_drops_collection(self, _patch_deps):
        from fleet_mem.server import clear_index

        _patch_deps["db"].has_collection.return_value = True

        result = await clear_index(path="/tmp/myproject")

        assert result["project"] == "myproject"
        assert result["status"] == "cleared"
        _patch_deps["db"].drop_collection.assert_called_once_with("code_myproject")

    @pytest.mark.asyncio
    async def test_no_collection_still_succeeds(self, _patch_deps):
        from fleet_mem.server import clear_index

        _patch_deps["db"].has_collection.return_value = False

        result = await clear_index(path="/tmp/myproject")

        assert result["status"] == "cleared"
        _patch_deps["db"].drop_collection.assert_not_called()


# ---------------------------------------------------------------------------
# get_index_status
# ---------------------------------------------------------------------------


class TestGetIndexStatus:
    @pytest.mark.asyncio
    async def test_not_indexed(self, _patch_deps):
        from fleet_mem.server import get_index_status

        _patch_deps["db"].has_collection.return_value = False

        result = await get_index_status(path="/tmp/myproject")

        assert result["status"] == "not_indexed"
        assert result["project"] == "myproject"

    @pytest.mark.asyncio
    async def test_indexed_from_db(self, _patch_deps):
        from fleet_mem.server import get_index_status

        _patch_deps["db"].has_collection.return_value = True
        _patch_deps["db"].count.return_value = 100

        result = await get_index_status(path="/tmp/myproject")

        assert result["status"] == "indexed"
        assert result["chunk_count"] == 100

    @pytest.mark.asyncio
    async def test_status_from_tracking_dict(self, _patch_deps):
        from fleet_mem.server import _index_status, get_index_status

        _index_status["myproject"] = {
            "status": "indexing",
            "file_count": 10,
            "chunk_count": 0,
            "last_sync": None,
            "error": None,
        }

        result = await get_index_status(path="/tmp/myproject")

        assert result["status"] == "indexing"


# ---------------------------------------------------------------------------
# find_symbol
# ---------------------------------------------------------------------------


class TestFindSymbol:
    @pytest.mark.asyncio
    async def test_returns_matches(self, _patch_deps):
        from fleet_mem.server import find_symbol

        _patch_deps["db"].list_collections.return_value = ["code_proj"]

        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "documents": ["def my_func(): pass"],
            "metadatas": [
                {
                    "file_path": "src/lib.py",
                    "start_line": 10,
                    "end_line": 15,
                    "chunk_type": "function",
                    "project_name": "proj",
                    "name": "my_func",
                }
            ],
        }
        _patch_deps["db"]._client.get_collection.return_value = mock_col

        results = await find_symbol(name="my_func")

        assert len(results) == 1
        assert results[0]["file_path"] == "src/lib.py"
        assert results[0]["symbol_type"] == "function"

    @pytest.mark.asyncio
    async def test_file_path_filter(self, _patch_deps):
        from fleet_mem.server import find_symbol

        _patch_deps["db"].list_collections.return_value = ["code_proj"]

        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1", "id2"],
            "documents": ["def f(): pass", "def f(): pass"],
            "metadatas": [
                {
                    "file_path": "a.py",
                    "start_line": 1,
                    "end_line": 2,
                    "chunk_type": "function",
                    "project_name": "proj",
                },
                {
                    "file_path": "b.py",
                    "start_line": 1,
                    "end_line": 2,
                    "chunk_type": "function",
                    "project_name": "proj",
                },
            ],
        }
        _patch_deps["db"]._client.get_collection.return_value = mock_col

        results = await find_symbol(name="f", file_path="a.py")
        assert len(results) == 1
        assert results[0]["file_path"] == "a.py"

    @pytest.mark.asyncio
    async def test_empty_results(self, _patch_deps):
        from fleet_mem.server import find_symbol

        _patch_deps["db"].list_collections.return_value = []
        results = await find_symbol(name="nonexistent")
        assert results == []


# ---------------------------------------------------------------------------
# get_change_impact
# ---------------------------------------------------------------------------


class TestGetChangeImpact:
    @pytest.mark.asyncio
    async def test_finds_impacted_chunks(self, _patch_deps):
        from fleet_mem.server import get_change_impact

        _patch_deps["db"].list_collections.return_value = ["code_proj"]

        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "documents": ["from utils import helper"],
            "metadatas": [
                {
                    "file_path": "src/main.py",
                    "start_line": 1,
                    "end_line": 5,
                    "project_name": "proj",
                }
            ],
        }
        _patch_deps["db"]._client.get_collection.return_value = mock_col

        results = await get_change_impact(symbol_names=["helper"])

        assert len(results) >= 1
        assert results[0]["matched_term"] == "helper"

    @pytest.mark.asyncio
    async def test_empty_inputs(self, _patch_deps):
        from fleet_mem.server import get_change_impact

        _patch_deps["db"].list_collections.return_value = []
        results = await get_change_impact()
        assert results == []


# ---------------------------------------------------------------------------
# get_dependents
# ---------------------------------------------------------------------------


class TestGetDependents:
    @pytest.mark.asyncio
    async def test_finds_dependents(self, _patch_deps):
        from fleet_mem.server import get_dependents

        _patch_deps["db"].list_collections.return_value = ["code_proj"]

        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "documents": ["x = my_func()"],
            "metadatas": [
                {
                    "file_path": "src/caller.py",
                    "start_line": 5,
                    "end_line": 10,
                    "project_name": "proj",
                }
            ],
        }
        _patch_deps["db"]._client.get_collection.return_value = mock_col

        results = await get_dependents(symbol_name="my_func")

        assert len(results) == 1
        assert results[0]["depth"] == 1
        assert results[0]["file_path"] == "src/caller.py"

    @pytest.mark.asyncio
    async def test_skips_definition_file(self, _patch_deps):
        from fleet_mem.server import get_dependents

        _patch_deps["db"].list_collections.return_value = ["code_proj"]

        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "documents": ["def my_func(): pass"],
            "metadatas": [
                {
                    "file_path": "src/lib.py",
                    "start_line": 1,
                    "end_line": 5,
                    "project_name": "proj",
                }
            ],
        }
        _patch_deps["db"]._client.get_collection.return_value = mock_col

        results = await get_dependents(symbol_name="my_func", file_path="src/lib.py")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# find_similar_code
# ---------------------------------------------------------------------------


class TestFindSimilarCode:
    @pytest.mark.asyncio
    async def test_returns_similar_chunks(self, _patch_deps):
        from fleet_mem.server import find_similar_code

        _patch_deps["db"].list_collections.return_value = ["code_proj"]
        _patch_deps["db"].has_collection.return_value = True
        _patch_deps["db"].search.return_value = [
            {
                "id": "abc",
                "content": "def greet(name): return f'Hi {name}'",
                "score": 0.88,
                "metadata": {
                    "file_path": "src/greet.py",
                    "start_line": 1,
                    "end_line": 2,
                    "project_name": "proj",
                },
            }
        ]

        results = await find_similar_code(code_snippet="def hello(name): pass")

        assert len(results) == 1
        assert results[0]["score"] == 0.88
        assert results[0]["file_path"] == "src/greet.py"


# ---------------------------------------------------------------------------
# Memory tools
# ---------------------------------------------------------------------------


class TestMemorySearch:
    @pytest.mark.asyncio
    async def test_returns_results(self, _patch_deps):
        from fleet_mem.server import memory_search

        mock_mem = _patch_deps["memory"]
        mock_result = MagicMock()
        mock_result.id = "mem1"
        mock_result.node_type = "insight"
        mock_result.content = "test content"
        mock_result.summary = "test summary"
        mock_result.score = 0.9
        mock_result.file_path = "src/foo.py"
        mock_mem.memory_search.return_value = [mock_result]

        results = await memory_search(query="test")

        assert len(results) == 1
        assert results[0]["id"] == "mem1"
        assert results[0]["node_type"] == "insight"
        assert results[0]["score"] == 0.9


class TestMemoryStore:
    @pytest.mark.asyncio
    async def test_stores_and_returns_id(self, _patch_deps):
        from fleet_mem.server import memory_store

        mock_mem = _patch_deps["memory"]
        mock_mem.memory_store.return_value = "new-id-123"

        result = await memory_store(node_type="insight", content="learned something")

        assert result["id"] == "new-id-123"
        assert result["status"] == "stored"
        mock_mem.memory_store.assert_called_once()


class TestMemoryPromote:
    @pytest.mark.asyncio
    async def test_promotes(self, _patch_deps):
        from fleet_mem.server import memory_promote

        mock_mem = _patch_deps["memory"]

        result = await memory_promote(memory_id="mem1")

        assert result["status"] == "promoted"
        mock_mem.memory_promote.assert_called_once_with("mem1", target_scope=None)


class TestStaleCheck:
    @pytest.mark.asyncio
    async def test_returns_stale_anchors(self, _patch_deps):
        from fleet_mem.server import stale_check

        mock_mem = _patch_deps["memory"]
        mock_anchor = MagicMock()
        mock_anchor.memory_id = "mem1"
        mock_anchor.anchor_id = "anc1"
        mock_anchor.file_path = "src/old.py"
        mock_anchor.stored_hash = "aaa"
        mock_anchor.current_hash = "bbb"
        mock_mem.stale_check.return_value = [mock_anchor]

        results = await stale_check()

        assert len(results) == 1
        assert results[0]["memory_id"] == "mem1"
        assert results[0]["stored_hash"] == "aaa"
        assert results[0]["current_hash"] == "bbb"
