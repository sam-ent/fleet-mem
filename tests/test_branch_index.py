"""Tests for BranchIndex: overlay search precedence, branch isolation, cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fleet_mem.fleet.branch_index import BranchIndex, _sanitize_branch  # noqa: E402
from fleet_mem.vectordb.types import VectorDocument  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(collections: dict[str, list[dict]] | None = None) -> MagicMock:
    """Build a mock VectorDatabase.

    *collections* maps collection names to search results that will be
    returned by ``db.search()``.
    """
    db = MagicMock()
    _cols = collections or {}

    db.has_collection.side_effect = lambda name: name in _cols
    db.list_collections.return_value = list(_cols.keys())
    db.count.side_effect = lambda name: len(_cols.get(name, []))

    def _search(collection, vector, limit=10, where=None):
        return _cols.get(collection, [])[:limit]

    db.search.side_effect = _search
    return db


def _hit(file_path: str, score: float, content: str = "") -> dict:
    return {
        "id": file_path,
        "content": content,
        "score": score,
        "metadata": {"file_path": file_path, "project_name": "proj"},
    }


# ---------------------------------------------------------------------------
# _sanitize_branch
# ---------------------------------------------------------------------------


class TestSanitizeBranch:
    def test_replaces_slashes(self):
        assert _sanitize_branch("feat/auth") == "feat--auth"

    def test_strips_special_chars(self):
        assert _sanitize_branch("fix/bug#123") == "fix--bug123"

    def test_simple_name_unchanged(self):
        assert _sanitize_branch("main") == "main"


# ---------------------------------------------------------------------------
# BranchIndex naming
# ---------------------------------------------------------------------------


class TestBranchIndexNaming:
    def test_base_collection(self):
        bi = BranchIndex(MagicMock(), "myproj")
        assert bi.base_collection == "code_myproj"

    def test_overlay_collection(self):
        bi = BranchIndex(MagicMock(), "myproj")
        assert bi.overlay_collection("feat/auth") == "code_myproj__feat--auth"


# ---------------------------------------------------------------------------
# Search: overlay precedence
# ---------------------------------------------------------------------------


class TestSearchOverlayPrecedence:
    def test_overlay_results_take_priority(self):
        """When both overlay and base have results for the same file,
        the overlay result wins and the base result is excluded."""
        db = _make_db(
            {
                "code_proj": [_hit("src/a.py", 0.8, "base version")],
                "code_proj__feat--x": [_hit("src/a.py", 0.9, "overlay version")],
            }
        )
        bi = BranchIndex(db, "proj")
        results = bi.search([0.1] * 8, branch="feat/x", limit=10)

        assert len(results) == 1
        assert results[0]["content"] == "overlay version"
        assert results[0]["score"] == 0.9

    def test_base_results_included_for_different_files(self):
        """Base results for files NOT in the overlay are included."""
        db = _make_db(
            {
                "code_proj": [_hit("src/b.py", 0.7, "base b")],
                "code_proj__feat--x": [_hit("src/a.py", 0.9, "overlay a")],
            }
        )
        bi = BranchIndex(db, "proj")
        results = bi.search([0.1] * 8, branch="feat/x", limit=10)

        assert len(results) == 2
        # Sorted by score desc
        assert results[0]["metadata"]["file_path"] == "src/a.py"
        assert results[1]["metadata"]["file_path"] == "src/b.py"

    def test_no_branch_searches_base_only(self):
        db = _make_db(
            {
                "code_proj": [_hit("src/a.py", 0.8)],
                "code_proj__feat--x": [_hit("src/a.py", 0.9)],
            }
        )
        bi = BranchIndex(db, "proj")
        results = bi.search([0.1] * 8, branch=None, limit=10)

        assert len(results) == 1
        assert results[0]["score"] == 0.8

    def test_missing_overlay_falls_back_to_base(self):
        db = _make_db({"code_proj": [_hit("src/a.py", 0.8)]})
        bi = BranchIndex(db, "proj")
        results = bi.search([0.1] * 8, branch="feat/new", limit=10)

        assert len(results) == 1


# ---------------------------------------------------------------------------
# Branch isolation
# ---------------------------------------------------------------------------


class TestBranchIsolation:
    def test_different_branches_have_separate_collections(self):
        db = _make_db(
            {
                "code_proj__feat--a": [_hit("src/a.py", 0.9)],
                "code_proj__feat--b": [_hit("src/b.py", 0.8)],
                "code_proj": [],
            }
        )
        bi = BranchIndex(db, "proj")

        results_a = bi.search([0.1] * 8, branch="feat/a", limit=10)
        results_b = bi.search([0.1] * 8, branch="feat/b", limit=10)

        assert len(results_a) == 1
        assert results_a[0]["metadata"]["file_path"] == "src/a.py"
        assert len(results_b) == 1
        assert results_b[0]["metadata"]["file_path"] == "src/b.py"


# ---------------------------------------------------------------------------
# Cleanup on merge
# ---------------------------------------------------------------------------


class TestCleanupOnMerge:
    def test_drop_branch_removes_overlay(self):
        db = _make_db({"code_proj__feat--x": []})
        bi = BranchIndex(db, "proj")

        assert bi.drop_branch("feat/x") is True
        db.drop_collection.assert_called_once_with("code_proj__feat--x")

    def test_drop_nonexistent_branch_returns_false(self):
        db = _make_db({})
        bi = BranchIndex(db, "proj")

        assert bi.drop_branch("feat/gone") is False
        db.drop_collection.assert_not_called()


# ---------------------------------------------------------------------------
# list_branches
# ---------------------------------------------------------------------------


class TestListBranches:
    def test_lists_overlay_collections(self):
        db = _make_db(
            {
                "code_proj": [_hit("a", 1)] * 10,
                "code_proj__feat--a": [_hit("b", 1)] * 3,
                "code_proj__fix--bug": [_hit("c", 1)] * 7,
                "code_other__feat--x": [],  # different project
            }
        )
        bi = BranchIndex(db, "proj")
        branches = bi.list_branches()

        names = {b["branch"] for b in branches}
        assert names == {"feat--a", "fix--bug"}
        by_name = {b["branch"]: b["chunk_count"] for b in branches}
        assert by_name["feat--a"] == 3
        assert by_name["fix--bug"] == 7

    def test_empty_when_no_overlays(self):
        db = _make_db({"code_proj": []})
        bi = BranchIndex(db, "proj")
        assert bi.list_branches() == []


# ---------------------------------------------------------------------------
# index_branch
# ---------------------------------------------------------------------------


class TestIndexBranch:
    def test_inserts_only_changed_files(self):
        db = MagicMock()
        db.has_collection.return_value = False
        bi = BranchIndex(db, "proj")

        docs = [
            VectorDocument(
                id="1",
                content="a",
                metadata={"file_path": "src/a.py"},
                vector=[0.1] * 8,
            ),
            VectorDocument(
                id="2",
                content="b",
                metadata={"file_path": "src/b.py"},
                vector=[0.2] * 8,
            ),
            VectorDocument(
                id="3",
                content="c",
                metadata={"file_path": "src/c.py"},
                vector=[0.3] * 8,
            ),
        ]

        count = bi.index_branch("feat/x", ["src/a.py", "src/c.py"], docs)

        assert count == 2
        db.create_collection.assert_called_once_with("code_proj__feat--x", 8)
        inserted = db.insert.call_args[0][1]
        paths = {d.metadata["file_path"] for d in inserted}
        assert paths == {"src/a.py", "src/c.py"}

    def test_empty_chunks_returns_zero(self):
        db = MagicMock()
        bi = BranchIndex(db, "proj")
        assert bi.index_branch("feat/x", ["a.py"], []) == 0

    def test_no_matching_files_returns_zero(self):
        db = MagicMock()
        bi = BranchIndex(db, "proj")
        docs = [
            VectorDocument(
                id="1",
                content="a",
                metadata={"file_path": "src/z.py"},
                vector=[0.1] * 8,
            ),
        ]
        count = bi.index_branch("feat/x", ["src/a.py"], docs)
        assert count == 0


# ---------------------------------------------------------------------------
# get_changed_files
# ---------------------------------------------------------------------------


class TestGetChangedFiles:
    def test_parses_git_diff_output(self):
        db = MagicMock()
        bi = BranchIndex(db, "proj")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "src/a.py\nsrc/b.py\n"

        patch_target = "fleet_mem.fleet.branch_index.subprocess.run"
        with patch(patch_target, return_value=mock_result) as mock_run:
            files = bi.get_changed_files("/tmp/proj", "feat/x")

        assert files == ["src/a.py", "src/b.py"]
        mock_run.assert_called_once_with(
            ["git", "diff", "--name-only", "main...feat/x"],
            capture_output=True,
            text=True,
            cwd="/tmp/proj",
        )

    def test_returns_empty_on_failure(self):
        db = MagicMock()
        bi = BranchIndex(db, "proj")

        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""

        with patch("fleet_mem.fleet.branch_index.subprocess.run", return_value=mock_result):
            files = bi.get_changed_files("/tmp/proj", "feat/x")

        assert files == []
