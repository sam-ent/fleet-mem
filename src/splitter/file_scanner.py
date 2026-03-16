"""Walk a codebase, respect .gitignore, yield files with language info."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterator

# Extensions to language mapping
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".txt": "text",
    ".sh": "shell",
    ".bash": "shell",
    ".css": "css",
    ".html": "html",
    ".sql": "sql",
    ".dockerfile": "dockerfile",
}

# Always skip these directories
_SKIP_DIRS: set[str] = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",  # Rust
    ".eggs",
    "*.egg-info",
}


def _parse_gitignore(gitignore_path: Path) -> list[str]:
    """Parse a .gitignore file into a list of patterns."""
    if not gitignore_path.is_file():
        return []
    patterns = []
    for line in gitignore_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _is_ignored(
    path: Path,
    root: Path,
    ignore_patterns: list[str],
) -> bool:
    """Check if a path matches any gitignore pattern."""
    rel = str(path.relative_to(root))
    rel_with_slash = rel + "/" if path.is_dir() else rel
    name = path.name

    for pattern in ignore_patterns:
        # Directory-only pattern (trailing /)
        clean = pattern.rstrip("/")

        # Match against the relative path
        if fnmatch.fnmatch(rel, clean):
            return True
        if fnmatch.fnmatch(rel_with_slash, pattern):
            return True
        # Match against just the filename/dirname
        if fnmatch.fnmatch(name, clean):
            return True
        # Match with leading ** for nested patterns
        if "/" not in clean and fnmatch.fnmatch(name, clean):
            return True

    return False


def scan_files(
    root: Path,
    *,
    extra_ignore_patterns: list[str] | None = None,
    supported_extensions: set[str] | None = None,
) -> Iterator[tuple[Path, str, str]]:
    """Walk a codebase and yield (path, language, content) tuples.

    Args:
        root: Root directory to scan.
        extra_ignore_patterns: Additional gitignore-style patterns to skip.
        supported_extensions: If provided, only yield files with these extensions.
            Defaults to all keys in EXTENSION_MAP.

    Yields:
        (absolute_path, language, file_content) for each matching file.
    """
    root = root.resolve()
    if not root.is_dir():
        return

    max_file_size = 1_048_576  # 1 MB
    exts = supported_extensions or set(EXTENSION_MAP.keys())

    # Collect gitignore patterns
    ignore_patterns = _parse_gitignore(root / ".gitignore")
    if extra_ignore_patterns:
        ignore_patterns.extend(extra_ignore_patterns)

    def _walk(directory: Path) -> Iterator[tuple[Path, str, str]]:
        try:
            entries = sorted(directory.iterdir())
        except PermissionError:
            return

        for entry in entries:
            # Skip hidden dirs and known skip dirs
            if entry.is_dir():
                if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                    continue
                if _is_ignored(entry, root, ignore_patterns):
                    continue
                # Check for nested .gitignore (not implemented for simplicity)
                yield from _walk(entry)
            elif entry.is_symlink():
                continue
            elif entry.is_file():
                if _is_ignored(entry, root, ignore_patterns):
                    continue
                suffix = entry.suffix.lower()
                if suffix not in exts:
                    continue
                if entry.stat().st_size > max_file_size:
                    continue
                language = EXTENSION_MAP.get(suffix, "unknown")
                try:
                    content = entry.read_text(errors="replace")
                except (PermissionError, OSError):
                    continue
                yield (entry, language, content)

    yield from _walk(root)
