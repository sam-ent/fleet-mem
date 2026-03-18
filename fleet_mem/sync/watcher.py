"""File watcher for near-instant sync using OS-native events."""

import threading
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path

import structlog
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = structlog.get_logger(__name__)

# Default ignore patterns
_IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    "*.egg-info",
}

_DEBOUNCE_SECONDS = 0.5


class _DebouncedHandler(FileSystemEventHandler):
    """Collects file events and debounces them before triggering callback."""

    def __init__(
        self,
        callback: Callable[[list[str], list[str]], None],
        root: Path,
        ignore_patterns: list[str] | None = None,
    ):
        self._callback = callback
        self._root = root
        self._ignore_patterns = ignore_patterns or []
        self._pending_changed: set[str] = set()
        self._pending_removed: set[str] = set()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _should_ignore(self, path: str) -> bool:
        rel = str(Path(path).relative_to(self._root))
        parts = Path(rel).parts
        for part in parts:
            if part in _IGNORE_DIRS:
                return True
            for pattern in self._ignore_patterns:
                if fnmatch(part, pattern):
                    return True
        return False

    def on_modified(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        self._add_changed(event.src_path)

    def on_created(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        self._add_changed(event.src_path)

    def on_deleted(self, event: FileSystemEvent):
        if event.is_directory or self._should_ignore(event.src_path):
            return
        self._add_removed(event.src_path)

    def on_moved(self, event: FileSystemEvent):
        if event.is_directory:
            return
        if not self._should_ignore(event.src_path):
            self._add_removed(event.src_path)
        if not self._should_ignore(event.dest_path):
            self._add_changed(event.dest_path)

    def _add_changed(self, path: str):
        with self._lock:
            rel = str(Path(path).relative_to(self._root))
            self._pending_changed.add(rel)
            self._pending_removed.discard(rel)
            self._reset_timer()

    def _add_removed(self, path: str):
        with self._lock:
            rel = str(Path(path).relative_to(self._root))
            self._pending_removed.add(rel)
            self._pending_changed.discard(rel)
            self._reset_timer()

    def _reset_timer(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._flush)
        self._timer.daemon = True
        self._timer.start()

    def _flush(self):
        with self._lock:
            changed = sorted(self._pending_changed)
            removed = sorted(self._pending_removed)
            self._pending_changed.clear()
            self._pending_removed.clear()

        if changed or removed:
            logger.info("Watcher detected: %d changed, %d removed", len(changed), len(removed))
            try:
                self._callback(changed, removed)
            except Exception:
                logger.exception("Watcher callback error")


class FileWatcher:
    """Watches project directories for file changes using OS-native events."""

    def __init__(self):
        self._observers: dict[str, Observer] = {}  # project_name -> Observer

    def watch(
        self,
        project_name: str,
        project_path: Path,
        callback: Callable[[list[str], list[str]], None],
        ignore_patterns: list[str] | None = None,
    ):
        """Start watching a project directory."""
        if project_name in self._observers:
            return  # already watching

        handler = _DebouncedHandler(callback, project_path, ignore_patterns)
        observer = Observer()
        observer.schedule(handler, str(project_path), recursive=True)
        observer.daemon = True
        observer.start()
        self._observers[project_name] = observer
        logger.info("Watching %s at %s", project_name, project_path)

    def unwatch(self, project_name: str):
        """Stop watching a project."""
        observer = self._observers.pop(project_name, None)
        if observer:
            observer.stop()
            observer.join(timeout=2)

    def stop_all(self):
        """Stop all watchers."""
        for name in list(self._observers):
            self.unwatch(name)
