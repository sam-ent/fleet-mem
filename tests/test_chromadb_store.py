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
