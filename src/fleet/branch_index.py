"""Branch/worktree-aware collection management for ChromaDB.

Maintains a base collection ``code_{project}`` for the main branch and
overlay collections ``code_{project}__{branch}`` that hold only chunks
differing from main.  Search merges overlay results (higher priority)
with base results, deduplicating by file path.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from src.vectordb.base import VectorDatabase


def _sanitize_branch(branch: str) -> str:
    """Sanitize a git branch name for use in a ChromaDB collection name.

    Replaces ``/`` with ``--`` and strips characters that are not
    alphanumeric, hyphen, or underscore.
    """
    name = branch.replace("/", "--")
    name = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    return name


class BranchIndex:
    """Manage per-branch overlay collections on top of a base collection."""

    def __init__(self, db: VectorDatabase, project: str) -> None:
        self._db = db
        self._project = project

    # -- naming helpers -----------------------------------------------------

    @property
    def base_collection(self) -> str:
        return f"code_{self._project}"

    def overlay_collection(self, branch: str) -> str:
        return f"code_{self._project}__{_sanitize_branch(branch)}"

    # -- indexing ------------------------------------------------------------

    def get_changed_files(self, project_path: str | Path, branch: str) -> list[str]:
        """Return files that differ between *main* and *branch*."""
        result = subprocess.run(
            ["git", "diff", "--name-only", f"main...{branch}"],
            capture_output=True,
            text=True,
            cwd=str(project_path),
        )
        if result.returncode != 0:
            return []
        return [f for f in result.stdout.strip().splitlines() if f]

    def index_branch(
        self,
        branch: str,
        changed_files: list[str],
        chunks: list,
    ) -> int:
        """Insert *chunks* (``VectorDocument`` instances) into the overlay collection.

        Only chunks whose ``file_path`` metadata is in *changed_files* are
        inserted.  Returns the number of chunks stored.
        """
        col_name = self.overlay_collection(branch)
        if not chunks:
            return 0

        # Determine dimension from first chunk that has a vector
        dim = 0
        for c in chunks:
            if c.vector is not None:
                dim = len(c.vector)
                break
        if dim == 0:
            return 0

        self._db.create_collection(col_name, dim)

        changed_set = set(changed_files)
        to_insert = [c for c in chunks if c.metadata and c.metadata.get("file_path") in changed_set]
        if not to_insert:
            return 0

        self._db.insert(col_name, to_insert)
        return len(to_insert)

    # -- search -------------------------------------------------------------

    def search(
        self,
        query_vector: list[float],
        branch: str | None = None,
        limit: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """Search with overlay-first precedence.

        If *branch* is given and an overlay collection exists, search it
        first.  Then search the base collection, excluding file paths
        already covered by overlay results.  Merge and return the top
        *limit* results sorted by score descending.
        """
        overlay_results: list[dict] = []
        seen_paths: set[str] = set()

        if branch:
            col_name = self.overlay_collection(branch)
            if self._db.has_collection(col_name):
                overlay_results = self._db.search(
                    col_name,
                    vector=query_vector,
                    limit=limit,
                    where=where,
                )
                for hit in overlay_results:
                    fp = (hit.get("metadata") or {}).get("file_path")
                    if fp:
                        seen_paths.add(fp)

        # Search base
        base_results: list[dict] = []
        if self._db.has_collection(self.base_collection):
            base_results = self._db.search(
                self.base_collection,
                vector=query_vector,
                limit=limit,
                where=where,
            )

        # Exclude base results whose file_path is already in overlay
        filtered_base = []
        for hit in base_results:
            fp = (hit.get("metadata") or {}).get("file_path")
            if fp not in seen_paths:
                filtered_base.append(hit)

        merged = overlay_results + filtered_base
        merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return merged[:limit]

    # -- cleanup ------------------------------------------------------------

    def drop_branch(self, branch: str) -> bool:
        """Drop the overlay collection for *branch*.  Returns True if dropped."""
        col_name = self.overlay_collection(branch)
        if self._db.has_collection(col_name):
            self._db.drop_collection(col_name)
            return True
        return False

    # -- listing ------------------------------------------------------------

    def list_branches(self) -> list[dict[str, int | str]]:
        """List indexed branches with chunk counts.

        Returns a list of dicts with ``branch`` (sanitized name) and
        ``chunk_count``.
        """
        prefix = f"code_{self._project}__"
        results: list[dict[str, int | str]] = []
        for col_name in self._db.list_collections():
            if col_name.startswith(prefix):
                branch_part = col_name[len(prefix) :]
                count = self._db.count(col_name)
                results.append({"branch": branch_part, "chunk_count": count})
        return results
