"""Tests for FileSynchronizer and BackgroundSync."""

from unittest.mock import MagicMock

import pytest

from src.config import Config
from src.sync.background import BackgroundSync
from src.sync.synchronizer import FileSynchronizer


@pytest.fixture
def tmp_config(tmp_path):
    """Config pointing at tmp directories."""
    config = Config.__new__(Config)
    config.chroma_path = tmp_path / "chroma"
    config.merkle_path = tmp_path / "merkle"
    config.sync_interval_seconds = 300
    config.ollama_host = "http://localhost:11434"
    config.ollama_embed_model = "nomic-embed-text"
    config.memory_db_path = tmp_path / "memory.db"
    config.merkle_path.mkdir(parents=True)
    return config


@pytest.fixture
def tmp_project(tmp_path):
    """A small project directory with some files."""
    proj = tmp_path / "myproject"
    proj.mkdir()
    (proj / "main.py").write_text("print('hello')")
    (proj / "utils.py").write_text("x = 1")
    sub = proj / "sub"
    sub.mkdir()
    (sub / "mod.py").write_text("y = 2")
    return proj


class TestFileSynchronizer:
    def test_scan_returns_snapshot_format(self, tmp_config, tmp_project):
        sync = FileSynchronizer(tmp_config)
        snap = sync.scan(tmp_project)
        assert "files" in snap
        assert "root_hash" in snap
        assert "timestamp" in snap
        assert isinstance(snap["files"], dict)
        assert len(snap["files"]) == 3

    def test_scan_hashes_are_xxhash(self, tmp_config, tmp_project):
        sync = FileSynchronizer(tmp_config)
        snap = sync.scan(tmp_project)
        for h in snap["files"].values():
            assert len(h) == 16  # xxh3_64 hex length

    def test_scan_ignores_pycache(self, tmp_config, tmp_project):
        cache = tmp_project / "__pycache__"
        cache.mkdir()
        (cache / "main.cpython-312.pyc").write_bytes(b"\x00")
        sync = FileSynchronizer(tmp_config)
        snap = sync.scan(tmp_project)
        assert not any("__pycache__" in k for k in snap["files"])

    def test_scan_respects_custom_ignore(self, tmp_config, tmp_project):
        sync = FileSynchronizer(tmp_config, ignore_patterns={"sub"})
        snap = sync.scan(tmp_project)
        assert not any("sub" in k for k in snap["files"])
        assert len(snap["files"]) == 2

    def test_save_and_load_snapshot(self, tmp_config):
        sync = FileSynchronizer(tmp_config)
        snap = {"files": {"a.py": "abc123"}, "root_hash": "xyz", "timestamp": "now"}
        sync.save_snapshot("testproj", snap)
        loaded = sync.load_snapshot("testproj")
        assert loaded == snap

    def test_load_missing_snapshot_returns_none(self, tmp_config):
        sync = FileSynchronizer(tmp_config)
        assert sync.load_snapshot("nonexistent") is None

    def test_scan_detects_content_change(self, tmp_config, tmp_project):
        sync = FileSynchronizer(tmp_config)
        snap1 = sync.scan(tmp_project)
        (tmp_project / "main.py").write_text("print('changed')")
        snap2 = sync.scan(tmp_project)
        assert snap1["files"]["main.py"] != snap2["files"]["main.py"]
        assert snap1["root_hash"] != snap2["root_hash"]


class TestBackgroundSync:
    def test_sync_detects_new_file(self, tmp_config, tmp_project):
        callback = MagicMock()
        bg = BackgroundSync(tmp_config, tmp_project, "proj", callback)

        # First sync: everything is "added" (no prior snapshot)
        bg.sync_now()
        changed, removed = callback.call_args[0]
        assert len(changed) == 3
        assert len(removed) == 0

        # Add a file and sync again
        callback.reset_mock()
        (tmp_project / "new.py").write_text("new")
        bg.sync_now()
        changed, removed = callback.call_args[0]
        assert "new.py" in changed

    def test_sync_detects_removed_file(self, tmp_config, tmp_project):
        callback = MagicMock()
        bg = BackgroundSync(tmp_config, tmp_project, "proj", callback)

        bg.sync_now()  # baseline
        callback.reset_mock()

        (tmp_project / "utils.py").unlink()
        bg.sync_now()
        changed, removed = callback.call_args[0]
        assert "utils.py" in removed

    def test_sync_detects_modified_file(self, tmp_config, tmp_project):
        callback = MagicMock()
        bg = BackgroundSync(tmp_config, tmp_project, "proj", callback)

        bg.sync_now()  # baseline
        callback.reset_mock()

        (tmp_project / "main.py").write_text("print('modified')")
        bg.sync_now()
        changed, removed = callback.call_args[0]
        assert "main.py" in changed
        assert len(removed) == 0

    def test_no_changes_no_callback(self, tmp_config, tmp_project):
        callback = MagicMock()
        bg = BackgroundSync(tmp_config, tmp_project, "proj", callback)

        bg.sync_now()  # baseline
        callback.reset_mock()

        bg.sync_now()  # no changes
        callback.assert_not_called()

    def test_start_stop(self, tmp_config, tmp_project):
        callback = MagicMock()
        bg = BackgroundSync(tmp_config, tmp_project, "proj", callback)
        bg.start()
        assert bg._running is True
        assert bg._timer is not None
        bg.stop()
        assert bg._running is False
        assert bg._timer is None
