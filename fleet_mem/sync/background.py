"""Background sync: periodic file change detection and re-indexing."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import structlog

from fleet_mem.config import Config
from fleet_mem.sync.merkle import MerkleDAG
from fleet_mem.sync.synchronizer import FileSynchronizer

logger = structlog.get_logger(__name__)


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
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background sync loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the background sync loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        """Run sync cycles at the configured interval."""
        while self._running:
            await asyncio.sleep(self._config.sync_interval_seconds)
            try:
                await self._sync_once()
            except Exception:
                logger.exception("Background sync error")

    async def _sync_once(self) -> None:
        """Compare current state to saved snapshot, invoke callback for diffs."""
        old_snapshot = await asyncio.to_thread(self._synchronizer.load_snapshot, self._project_name)
        new_snapshot = await asyncio.to_thread(self._synchronizer.scan, self._project_path)

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
            await asyncio.to_thread(self._reindex_callback, changed, removed)

        await asyncio.to_thread(self._synchronizer.save_snapshot, self._project_name, new_snapshot)

    async def sync_now(self) -> None:
        """Run a sync cycle immediately (useful for testing)."""
        await self._sync_once()
