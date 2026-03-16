"""Tests for ChunkReconciler ghost chunk cleanup."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.sync.reconciler import ChunkReconciler
from src.vectordb.chromadb_store import ChromaDBStore


@pytest.fixture()
def db(tmp_path: Path) -> ChromaDBStore:
    return ChromaDBStore(tmp_path / "chroma")


def _insert_chunks(db: ChromaDBStore, collection: str, file_path: str, count: int = 3):
    """Insert dummy chunks for a file."""
    from src.vectordb.types import VectorDocument

    dim = 8
    db.create_collection(collection, dim)
    docs = []
    for i in range(count):
        docs.append(
            VectorDocument(
                id=f"{file_path}:{i}",
                content=f"chunk {i} of {file_path}",
                metadata={"file_path": file_path, "start_line": i, "end_line": i + 10},
                vector=np.random.default_rng(42 + i).random(dim).tolist(),
            )
        )
    db.insert(collection, docs)


class TestReconcileFile:
    def test_deletes_chunks_for_target_file(self, db: ChromaDBStore):
        col = "code_test"
        _insert_chunks(db, col, "file_a.py", 3)
        _insert_chunks(db, col, "file_b.py", 2)
        assert db.count(col) == 5

        reconciler = ChunkReconciler(db)
        reconciler.reconcile_file(col, "file_a.py")

        assert db.count(col) == 2
        # Remaining chunks belong to file_b
        remaining = db._client.get_collection(col).get(include=["metadatas"])
        for meta in remaining["metadatas"]:
            assert meta["file_path"] == "file_b.py"

    def test_noop_when_file_not_present(self, db: ChromaDBStore):
        col = "code_test"
        _insert_chunks(db, col, "file_a.py", 2)
        reconciler = ChunkReconciler(db)
        reconciler.reconcile_file(col, "nonexistent.py")
        assert db.count(col) == 2


class TestReconcileRemovedFiles:
    def test_deletes_multiple_files(self, db: ChromaDBStore):
        col = "code_test"
        _insert_chunks(db, col, "file_a.py", 2)
        _insert_chunks(db, col, "file_b.py", 2)
        _insert_chunks(db, col, "file_c.py", 2)
        assert db.count(col) == 6

        reconciler = ChunkReconciler(db)
        reconciler.reconcile_removed_files(col, ["file_a.py", "file_b.py"])

        assert db.count(col) == 2
        remaining = db._client.get_collection(col).get(include=["metadatas"])
        for meta in remaining["metadatas"]:
            assert meta["file_path"] == "file_c.py"


class TestFullReconcile:
    def test_removes_orphans_returns_count(self, db: ChromaDBStore):
        col = "code_test"
        _insert_chunks(db, col, "file_a.py", 3)
        _insert_chunks(db, col, "file_b.py", 2)

        reconciler = ChunkReconciler(db)
        removed = reconciler.full_reconcile(col, existing_files={"file_a.py"})

        assert removed == 2
        assert db.count(col) == 3

    def test_no_orphans_returns_zero(self, db: ChromaDBStore):
        col = "code_test"
        _insert_chunks(db, col, "file_a.py", 2)

        reconciler = ChunkReconciler(db)
        removed = reconciler.full_reconcile(col, existing_files={"file_a.py"})

        assert removed == 0
        assert db.count(col) == 2

    def test_all_orphans(self, db: ChromaDBStore):
        col = "code_test"
        _insert_chunks(db, col, "file_a.py", 2)
        _insert_chunks(db, col, "file_b.py", 3)

        reconciler = ChunkReconciler(db)
        removed = reconciler.full_reconcile(col, existing_files=set())

        assert removed == 5
        assert db.count(col) == 0
