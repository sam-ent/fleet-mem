"""Tests for FileWatcher with debounced OS-native file watching."""

import time
from pathlib import Path
from unittest.mock import MagicMock

from fleet_mem.sync.watcher import FileWatcher


def test_create_triggers_changed(tmp_path: Path):
    cb = MagicMock()
    watcher = FileWatcher()
    watcher.watch("test_proj", tmp_path, cb)

    try:
        (tmp_path / "hello.py").write_text("print('hi')")
        time.sleep(1.5)

        cb.assert_called()
        changed, removed = cb.call_args[0]
        assert "hello.py" in changed
        assert removed == []
    finally:
        watcher.stop_all()


def test_delete_triggers_removed(tmp_path: Path):
    f = tmp_path / "bye.py"
    f.write_text("x = 1")
    time.sleep(0.2)

    cb = MagicMock()
    watcher = FileWatcher()
    watcher.watch("test_proj", tmp_path, cb)

    try:
        f.unlink()
        time.sleep(1.5)

        cb.assert_called()
        changed, removed = cb.call_args[0]
        assert "bye.py" in removed
    finally:
        watcher.stop_all()


def test_modify_triggers_changed(tmp_path: Path):
    f = tmp_path / "mod.py"
    f.write_text("x = 1")
    time.sleep(0.2)

    cb = MagicMock()
    watcher = FileWatcher()
    watcher.watch("test_proj", tmp_path, cb)

    try:
        f.write_text("x = 2")
        time.sleep(1.5)

        cb.assert_called()
        changed, _removed = cb.call_args[0]
        assert "mod.py" in changed
    finally:
        watcher.stop_all()


def test_debounce_batches_events(tmp_path: Path):
    cb = MagicMock()
    watcher = FileWatcher()
    watcher.watch("test_proj", tmp_path, cb)

    try:
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.py").write_text("b")
        (tmp_path / "c.py").write_text("c")
        time.sleep(1.5)

        # Should be batched into one call (or few), not three separate
        changed_files: set[str] = set()
        for call in cb.call_args_list:
            changed, _removed = call[0]
            changed_files.update(changed)
        assert {"a.py", "b.py", "c.py"} <= changed_files
    finally:
        watcher.stop_all()


def test_ignored_dirs_no_trigger(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    cb = MagicMock()
    watcher = FileWatcher()
    watcher.watch("test_proj", tmp_path, cb)

    try:
        (git_dir / "config").write_text("stuff")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.pyc").write_text("bytecode")
        time.sleep(1.5)

        cb.assert_not_called()
    finally:
        watcher.stop_all()


def test_unwatch_stops_observer(tmp_path: Path):
    cb = MagicMock()
    watcher = FileWatcher()
    watcher.watch("test_proj", tmp_path, cb)
    watcher.unwatch("test_proj")

    (tmp_path / "after.py").write_text("should not trigger")
    time.sleep(1.5)

    cb.assert_not_called()
