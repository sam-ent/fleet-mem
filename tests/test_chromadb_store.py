"""Tests for ChromaDB vector store."""

import pytest

from fleet_mem.vectordb.chromadb_store import ChromaDBStore
from fleet_mem.vectordb.types import VectorDocument

DIM = 8


def _vec(seed: float) -> list[float]:
    """Generate a deterministic 8-dim vector."""
    return [seed * (i + 1) * 0.1 for i in range(DIM)]


@pytest.fixture
def store(tmp_path):
    return ChromaDBStore(path=tmp_path / "chroma")


def test_create_and_has_collection(store):
    assert not store.has_collection("test")
    store.create_collection("test", dimension=DIM)
    assert store.has_collection("test")


def test_list_collections(store):
    store.create_collection("col-a", dimension=DIM)
    store.create_collection("col-b", dimension=DIM)
    names = store.list_collections()
    assert "col-a" in names
    assert "col-b" in names


def test_insert_and_search(store):
    store.create_collection("docs", dimension=DIM)
    docs = [
        VectorDocument(id="1", content="hello world", metadata={"lang": "en"}, vector=_vec(1.0)),
        VectorDocument(id="2", content="goodbye world", metadata={"lang": "en"}, vector=_vec(2.0)),
        VectorDocument(id="3", content="hola mundo", metadata={"lang": "es"}, vector=_vec(3.0)),
    ]
    store.insert("docs", docs)

    results = store.search("docs", vector=_vec(1.0), limit=2)
    assert len(results) == 2
    assert results[0]["id"] == "1"
    assert results[0]["content"] == "hello world"
    assert 0 < results[0]["score"] <= 1.0
    assert results[0]["metadata"]["lang"] == "en"


def test_search_with_where_filter(store):
    store.create_collection("docs", dimension=DIM)
    docs = [
        VectorDocument(id="1", content="hello", metadata={"lang": "en"}, vector=_vec(1.0)),
        VectorDocument(id="2", content="hola", metadata={"lang": "es"}, vector=_vec(2.0)),
    ]
    store.insert("docs", docs)

    results = store.search("docs", vector=_vec(1.0), limit=10, where={"lang": "es"})
    assert len(results) == 1
    assert results[0]["id"] == "2"


def test_count(store):
    store.create_collection("docs", dimension=DIM)
    assert store.count("docs") == 0
    store.insert(
        "docs",
        [
            VectorDocument(id="1", content="a", vector=_vec(1.0)),
        ],
    )
    assert store.count("docs") == 1


def test_delete(store):
    store.create_collection("docs", dimension=DIM)
    store.insert(
        "docs",
        [
            VectorDocument(id="1", content="a", vector=_vec(1.0)),
            VectorDocument(id="2", content="b", vector=_vec(2.0)),
        ],
    )
    store.delete("docs", ids=["1"])
    assert store.count("docs") == 1


def test_drop_collection(store):
    store.create_collection("docs", dimension=DIM)
    store.drop_collection("docs")
    assert not store.has_collection("docs")


def test_insert_missing_vector_raises(store):
    store.create_collection("docs", dimension=DIM)
    with pytest.raises(ValueError, match="missing pre-computed vectors"):
        store.insert("docs", [VectorDocument(id="1", content="a")])


def test_insert_dedupes_intra_batch_duplicate_ids(store):
    """Intra-batch duplicate IDs must not raise; last-wins semantics persist."""
    store.create_collection("docs", dimension=DIM)
    docs = [
        VectorDocument(
            id="dup", content="first version", metadata={"version": "1"}, vector=_vec(1.0)
        ),
        VectorDocument(
            id="dup", content="last version", metadata={"version": "2"}, vector=_vec(2.0)
        ),
        VectorDocument(id="distinct", content="other", metadata={"version": "1"}, vector=_vec(3.0)),
    ]
    # Without the dedupe shim, this raises chromadb.errors.DuplicateIDError.
    store.insert("docs", docs)

    # Both unique IDs persist (no batch abort).
    assert store.count("docs") == 2

    # Last-wins: the second "dup" entry overrode the first.
    results = store.search("docs", vector=_vec(2.0), limit=10)
    by_id = {r["id"]: r for r in results}
    assert "dup" in by_id
    assert by_id["dup"]["content"] == "last version"
    assert by_id["dup"]["metadata"]["version"] == "2"
    assert "distinct" in by_id
    assert by_id["distinct"]["content"] == "other"


def test_insert_preserves_distinct_ids(store):
    """Batches with all-distinct IDs must persist every document."""
    store.create_collection("docs", dimension=DIM)
    docs = [
        VectorDocument(id="a", content="alpha", vector=_vec(1.0)),
        VectorDocument(id="b", content="bravo", vector=_vec(2.0)),
        VectorDocument(id="c", content="charlie", vector=_vec(3.0)),
    ]
    store.insert("docs", docs)

    assert store.count("docs") == 3
    results = store.search("docs", vector=_vec(1.0), limit=10)
    assert {r["id"] for r in results} == {"a", "b", "c"}
