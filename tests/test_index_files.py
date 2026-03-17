"""Tests for index_files() and background sync re-indexing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _vec(seed: float = 1.0, dim: int = 8) -> list[float]:
    return [seed * (i + 1) * 0.1 for i in range(dim)]


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.has_collection.return_value = True
    db.count.return_value = 0
    return db


@pytest.fixture
def mock_embedder():
    emb = MagicMock()
    emb.embed.return_value = _vec()
    emb.embed_batch.side_effect = lambda texts: [_vec(i) for i in range(len(texts))]
    emb.get_dimension.return_value = 8
    return emb


class TestIndexFiles:
    """Tests for the index_files function."""

    def test_indexes_specific_files_only(self, tmp_path, mock_db, mock_embedder):
        """index_files processes only the listed files, not the whole directory."""
        from fleet_mem.indexer import index_files

        # Create 3 files but only index 1
        (tmp_path / "a.py").write_text("def hello():\n    return 1\n")
        (tmp_path / "b.py").write_text("def world():\n    return 2\n")
        (tmp_path / "c.py").write_text("x = 1\n")

        result = index_files(
            root=tmp_path,
            project_name="testproj",
            file_paths=["a.py"],
            db=mock_db,
            embedder=mock_embedder,
        )

        assert result.files_succeeded == 1
        assert result.files_failed == 0
        assert result.chunks_inserted > 0

        # Verify only a.py chunks were inserted
        insert_calls = mock_db.insert.call_args_list
        for call in insert_calls:
            docs = call[0][1]
            for doc in docs:
                assert doc.metadata["file_path"] == "a.py"

    def test_indexes_multiple_files(self, tmp_path, mock_db, mock_embedder):
        """index_files handles multiple files."""
        from fleet_mem.indexer import index_files

        (tmp_path / "a.py").write_text("def hello():\n    return 1\n")
        (tmp_path / "b.py").write_text("def world():\n    return 2\n")

        result = index_files(
            root=tmp_path,
            project_name="testproj",
            file_paths=["a.py", "b.py"],
            db=mock_db,
            embedder=mock_embedder,
        )

        assert result.files_succeeded == 2
        assert result.files_failed == 0

    def test_skips_missing_files_continues_with_rest(self, tmp_path, mock_db, mock_embedder):
        """A missing file is skipped; remaining files still get indexed."""
        from fleet_mem.indexer import index_files

        (tmp_path / "good.py").write_text("def ok():\n    return True\n")
        # "missing.py" does not exist

        result = index_files(
            root=tmp_path,
            project_name="testproj",
            file_paths=["missing.py", "good.py"],
            db=mock_db,
            embedder=mock_embedder,
        )

        assert result.files_succeeded == 1
        assert result.files_failed == 1
        assert "missing.py" in result.errors
        assert result.chunks_inserted > 0

    def test_skips_file_that_fails_to_parse(self, tmp_path, mock_db, mock_embedder):
        """If reading a file raises, that file is skipped."""
        from fleet_mem.indexer import index_files

        (tmp_path / "good.py").write_text("x = 1\n")
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("content")

        # Make bad.py unreadable by patching Path.read_text for that path
        original_read_text = Path.read_text

        def _patched_read_text(self, *args, **kwargs):
            if self.name == "bad.py":
                raise PermissionError("no access")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", _patched_read_text):
            result = index_files(
                root=tmp_path,
                project_name="testproj",
                file_paths=["bad.py", "good.py"],
                db=mock_db,
                embedder=mock_embedder,
            )

        assert result.files_succeeded == 1
        assert result.files_failed == 1
        assert "bad.py" in result.errors
        assert "no access" in result.errors["bad.py"]

    def test_empty_file_list_returns_zero(self, tmp_path, mock_db, mock_embedder):
        """No files means no work."""
        from fleet_mem.indexer import index_files

        result = index_files(
            root=tmp_path,
            project_name="testproj",
            file_paths=[],
            db=mock_db,
            embedder=mock_embedder,
        )

        assert result.chunks_inserted == 0
        assert result.files_succeeded == 0
        assert result.files_failed == 0
        mock_db.insert.assert_not_called()


class TestReindexCallback:
    """Tests for _make_reindex_callback re-indexing changed files."""

    def test_callback_reindexes_changed_files(self, tmp_path):
        """The callback deletes old chunks AND re-indexes changed files."""
        from fleet_mem.indexer import IndexFilesResult

        mock_config = MagicMock()
        mock_db = MagicMock()
        mock_db.has_collection.return_value = True
        mock_embedder = MagicMock()

        mock_index_result = IndexFilesResult(
            chunks_inserted=5,
            files_succeeded=2,
            files_failed=0,
            errors={},
        )

        with (
            patch("fleet_mem.server._get_db", return_value=mock_db),
            patch("fleet_mem.server._get_embedder", return_value=mock_embedder),
            patch("fleet_mem.indexer.index_files", return_value=mock_index_result) as mock_idx,
            patch("fleet_mem.server.Path") as mock_path_cls,
        ):
            # Make Path.home() / "CODE" / project point to a real-ish dir
            mock_project_dir = MagicMock()
            mock_project_dir.is_dir.return_value = True
            mock_code_dir = MagicMock()
            mock_code_dir.__truediv__ = MagicMock(return_value=mock_project_dir)
            mock_home = MagicMock()
            mock_home.__truediv__ = MagicMock(return_value=mock_code_dir)
            mock_path_cls.home.return_value = mock_home

            # Make Path(fp).parts work for grouping
            def _path_init(fp):
                p = MagicMock()
                p.parts = fp.split("/") if "/" in fp else (fp,)
                return p

            mock_path_cls.side_effect = _path_init

            from fleet_mem.server import _make_reindex_callback

            callback = _make_reindex_callback(mock_config)
            callback(
                changed_files=["myproj/src/a.py", "myproj/src/b.py"],
                removed_files=[],
            )

            # Verify reconciler deleted old chunks
            assert mock_db.delete_by_metadata.call_count >= 2

            # Verify index_files was called for re-indexing
            mock_idx.assert_called_once()
            call_kwargs = mock_idx.call_args
            assert call_kwargs[1]["project_name"] == "myproj"
            assert set(call_kwargs[1]["file_paths"]) == {
                "myproj/src/a.py",
                "myproj/src/b.py",
            }

    def test_callback_does_not_reindex_removed_files(self, tmp_path):
        """Removed files get chunks deleted but are NOT re-indexed."""
        mock_config = MagicMock()
        mock_db = MagicMock()
        mock_db.has_collection.return_value = True

        with (
            patch("fleet_mem.server._get_db", return_value=mock_db),
            patch("fleet_mem.server._get_embedder"),
            patch("fleet_mem.indexer.index_files") as mock_idx,
        ):
            from fleet_mem.server import _make_reindex_callback

            callback = _make_reindex_callback(mock_config)
            callback(changed_files=[], removed_files=["myproj/old.py"])

            # index_files should NOT be called (no changed files)
            mock_idx.assert_not_called()

            # But reconciler should delete removed file chunks
            mock_db.delete_by_metadata.assert_called()

    def test_callback_swallows_exceptions(self):
        """If the callback raises, it logs but does not propagate."""
        mock_config = MagicMock()

        with patch("fleet_mem.server._get_db", side_effect=RuntimeError("db broke")):
            from fleet_mem.server import _make_reindex_callback

            callback = _make_reindex_callback(mock_config)
            # Should not raise
            callback(changed_files=["proj/x.py"], removed_files=[])
