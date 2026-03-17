"""Background sync: periodic file change detection and re-indexing."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from src.config import Config
from src.sync.merkle import MerkleDAG
from src.sync.synchronizer import FileSynchronizer

logger = logging.getLogger(__name__)


class BackgroundSync:
    """Polls for file changes at a configurable interval, triggers re-indexing."""

    def __init__(
        self,
        config: Config,
        project_path: Path,
        project_name: str,
        reindex_callback: Callable[[list[str], list[str]], None],
    ):
        self._config = config
        self._project_path = project_path
        self._project_name = project_name
        self._reindex_callback = reindex_callback
        self._synchronizer = FileSynchronizer(config)
        self._timer: threading.Timer | None = None
        self._running = False

    def start(self) -> None:
        """Start the background sync loop."""
        if self._running:
            return
        self._running = True
        self._schedule()

    def stop(self) -> None:
        """Stop the background sync loop."""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self._config.sync_interval_seconds, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        """Run one sync cycle, then reschedule."""
        try:
            self._sync_once()
        except Exception:
            logger.exception("Background sync error")
        finally:
            self._schedule()

    def _sync_once(self) -> None:
        """Compare current state to saved snapshot, invoke callback for diffs."""
        old_snapshot = self._synchronizer.load_snapshot(self._project_name)
        new_snapshot = self._synchronizer.scan(self._project_path)

        if old_snapshot is None:
            old_tree = {"hash": "", "files": {}, "dirs": {}}
        elif "tree" in old_snapshot:
            old_tree = old_snapshot["tree"]
        else:
            # Legacy flat format: fall back to flat comparison
            old_tree = old_snapshot["files"]
        new_tree = new_snapshot["tree"]
        diff = MerkleDAG.compare(old_tree, new_tree)

        changed = sorted(diff["added"] | diff["modified"])
        removed = sorted(diff["removed"])

        if changed or removed:
            logger.info(
                "Sync detected changes: %d changed, %d removed",
                len(changed),
                len(removed),
            )
            self._reindex_callback(changed, removed)

        self._synchronizer.save_snapshot(self._project_name, new_snapshot)

    def sync_now(self) -> None:
        """Run a sync cycle immediately (useful for testing)."""
        self._sync_once()
