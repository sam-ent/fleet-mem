import asyncio
from unittest.mock import MagicMock, patch

import pytest

from fleet_mem.config import Config
from fleet_mem.sync.background import BackgroundSync


@pytest.fixture
def mock_config():
    config = MagicMock(spec=Config)
    config.sync_interval_seconds = 0.01
    return config


@pytest.fixture
def mock_reindex_callback():
    return MagicMock()


@pytest.fixture
def mock_sync_instance():
    with patch("fleet_mem.sync.background.FileSynchronizer") as MockSync:
        yield MockSync.return_value


@pytest.fixture
def bg_sync(mock_config, mock_reindex_callback, tmp_path, mock_sync_instance):
    return BackgroundSync(
        config=mock_config,
        project_path=tmp_path,
        project_name="test-project",
        reindex_callback=mock_reindex_callback,
    )


@pytest.mark.asyncio
async def test_start_stop(bg_sync):
    """Test starting and stopping the background sync loop."""
    await bg_sync.start()
    assert bg_sync._running is True
    assert bg_sync._task is not None

    task = bg_sync._task
    await bg_sync.stop()
    assert bg_sync._running is False
    assert bg_sync._task is None
    assert task.done() or task.cancelled()


@pytest.mark.asyncio
async def test_start_idempotent(bg_sync):
    """Test that start() is idempotent and does not create multiple tasks."""
    await bg_sync.start()
    task = bg_sync._task
    await bg_sync.start()
    assert bg_sync._task is task
    await bg_sync.stop()


@pytest.mark.asyncio
async def test_sync_now_no_changes(bg_sync, mock_sync_instance, mock_reindex_callback):
    """Test sync_now when no file changes are detected."""
    old_snap = {"tree": {"hash": "h1", "files": {}, "dirs": {}}}
    new_snap = {"tree": {"hash": "h1", "files": {}, "dirs": {}}}

    mock_sync_instance.load_snapshot.return_value = old_snap
    mock_sync_instance.scan.return_value = new_snap

    with patch("fleet_mem.sync.background.MerkleDAG.compare") as mock_compare:
        mock_compare.return_value = {"added": set(), "modified": set(), "removed": set()}
        await bg_sync.sync_now()

        mock_reindex_callback.assert_not_called()
        mock_sync_instance.save_snapshot.assert_called_once_with("test-project", new_snap)


@pytest.mark.asyncio
async def test_sync_now_with_changes(bg_sync, mock_sync_instance, mock_reindex_callback):
    """Test sync_now triggers re-index callback when changes are detected."""
    old_snap = {"tree": {"hash": "h1"}}
    new_snap = {"tree": {"hash": "h2"}}

    mock_sync_instance.load_snapshot.return_value = old_snap
    mock_sync_instance.scan.return_value = new_snap

    with patch("fleet_mem.sync.background.MerkleDAG.compare") as mock_compare:
        mock_compare.return_value = {
            "added": {"file_b.txt"},
            "modified": {"file_a.txt"},
            "removed": {"file_c.txt"},
        }
        await bg_sync.sync_now()

        # Lists passed to callback should be sorted
        mock_reindex_callback.assert_called_once_with(["file_a.txt", "file_b.txt"], ["file_c.txt"])
        mock_sync_instance.save_snapshot.assert_called_once_with("test-project", new_snap)


@pytest.mark.asyncio
async def test_sync_now_initial_run(bg_sync, mock_sync_instance):
    """Test initial sync when no previous snapshot exists."""
    mock_sync_instance.load_snapshot.return_value = None
    new_snap = {"tree": {"hash": "h1", "files": {}, "dirs": {}}}
    mock_sync_instance.scan.return_value = new_snap

    with patch("fleet_mem.sync.background.MerkleDAG.compare") as mock_compare:
        mock_compare.return_value = {"added": set(), "modified": set(), "removed": set()}
        await bg_sync.sync_now()

        # Should compare against a default empty tree
        mock_compare.assert_called_once_with(
            {"hash": "", "files": {}, "dirs": {}}, new_snap["tree"]
        )


@pytest.mark.asyncio
async def test_sync_now_legacy_format(bg_sync, mock_sync_instance):
    """Test sync handles legacy flat snapshot format."""
    old_snap = {"files": {"a.py": "h"}}
    new_snap = {"tree": {"hash": "h1"}}

    mock_sync_instance.load_snapshot.return_value = old_snap
    mock_sync_instance.scan.return_value = new_snap

    with patch("fleet_mem.sync.background.MerkleDAG.compare") as mock_compare:
        mock_compare.return_value = {"added": set(), "modified": set(), "removed": set()}
        await bg_sync.sync_now()

        # Should use "files" key directly for legacy comparison
        mock_compare.assert_called_once_with(old_snap["files"], new_snap["tree"])


@pytest.mark.asyncio
async def test_loop_handles_exception(bg_sync):
    """Test that the background loop survives exceptions in _sync_once."""
    call_count = 0
    done_event = asyncio.Event()

    async def tracked_sync():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("fail")
        # Signal that we've been called at least twice
        done_event.set()

    with patch.object(bg_sync, "_sync_once", side_effect=tracked_sync):
        await bg_sync.start()
        # Wait for the loop to have called _sync_once at least twice
        await asyncio.wait_for(done_event.wait(), timeout=5.0)
        await bg_sync.stop()
        assert call_count >= 2


@pytest.mark.asyncio
async def test_sync_now_propagates_error(bg_sync, mock_sync_instance):
    """Test that sync_now propagates internal exceptions."""
    mock_sync_instance.scan.side_effect = ValueError("scan error")
    with pytest.raises(ValueError, match="scan error"):
        await bg_sync.sync_now()


@pytest.mark.asyncio
async def test_stop_waits_for_cleanup(bg_sync):
    """Test that stop() waits for the background task to finish cancellation."""

    async def mock_loop():
        try:
            while bg_sync._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            await asyncio.sleep(0.01)  # Simulate cleanup work
            raise

    with patch.object(bg_sync, "_loop", side_effect=mock_loop):
        await bg_sync.start()
        task = bg_sync._task
        await bg_sync.stop()
        assert bg_sync._task is None
        assert task.done()
