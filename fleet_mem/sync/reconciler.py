"""Reconciles ChromaDB state with filesystem to remove ghost chunks."""

from __future__ import annotations

import structlog

from fleet_mem.vectordb.base import VectorDatabase

logger = structlog.get_logger(__name__)


class ChunkReconciler:
    """Reconciles ChromaDB state with filesystem."""

    def __init__(self, db: VectorDatabase):
        self._db = db

    def reconcile_file(self, collection: str, file_path: str) -> None:
        """Delete all chunks for a file. Called before re-inserting updated chunks."""
        self._db.delete_by_metadata(collection, "file_path", file_path)

    def reconcile_removed_files(self, collection: str, removed_files: list[str]) -> None:
        """Delete chunks for files that no longer exist."""
        for fp in removed_files:
            self._db.delete_by_metadata(collection, "file_path", fp)

    def full_reconcile(self, collection: str, existing_files: set[str]) -> int:
        """Scan all chunks, delete any whose source file no longer exists.

        Returns count of orphan chunks removed.
        """
        col = self._db._client.get_collection(name=collection)
        results = col.get(include=["metadatas"])

        ids = results.get("ids", [])
        metadatas = results.get("metadatas", [])

        orphan_ids: list[str] = []
        for i, doc_id in enumerate(ids):
            meta = metadatas[i] if metadatas else {}
            fp = meta.get("file_path", "") if meta else ""
            if fp not in existing_files:
                orphan_ids.append(doc_id)

        if orphan_ids:
            # ChromaDB delete has batch limits; delete in chunks of 5000
            for start in range(0, len(orphan_ids), 5000):
                batch = orphan_ids[start : start + 5000]
                col.delete(ids=batch)
            logger.info("Removed %d orphan chunks from %s", len(orphan_ids), collection)

        return len(orphan_ids)
