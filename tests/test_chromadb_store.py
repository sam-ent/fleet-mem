"""Tests for ChromaDB vector store."""

import pytest

from fleet_mem.vectordb.chromadb_store import ChromaDBStore
from fleet_mem.vectordb.errors import DimMismatchError
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


# --- Dim-mismatch detection (#46) -------------------------------------------


def _vec_n(seed: float, dim: int) -> list[float]:
    """Generate a deterministic ``dim``-dimension vector."""
    return [seed * (i + 1) * 0.1 for i in range(dim)]


def test_dim_validation_raises_on_insert_mismatch(store):
    """Inserting vectors of the wrong dim into a dim-locked collection
    must raise DimMismatchError before any chromadb work happens.
    """
    store.create_collection("docs", dimension=DIM)  # DIM == 8
    wrong_dim = DIM * 2  # 16
    docs = [
        VectorDocument(id="1", content="hello", vector=_vec_n(1.0, wrong_dim)),
    ]
    with pytest.raises(DimMismatchError) as excinfo:
        store.insert("docs", docs)
    msg = str(excinfo.value)
    assert "docs" in msg
    assert str(DIM) in msg
    assert str(wrong_dim) in msg


def test_dim_validation_raises_on_search_mismatch(store):
    """Searching with a wrong-dim query vector must raise DimMismatchError
    instead of silently returning ranked-but-meaningless results.
    """
    store.create_collection("docs", dimension=DIM)
    store.insert(
        "docs",
        [VectorDocument(id="1", content="hello", vector=_vec(1.0))],
    )
    wrong_dim = DIM * 2
    with pytest.raises(DimMismatchError):
        store.search("docs", vector=_vec_n(1.0, wrong_dim), limit=5)


def test_dim_validation_passes_on_match(store):
    """Happy path: matching dims must not raise."""
    store.create_collection("docs", dimension=DIM)
    docs = [VectorDocument(id="1", content="hello", vector=_vec(1.0))]
    store.insert("docs", docs)
    results = store.search("docs", vector=_vec(1.0), limit=5)
    assert len(results) == 1
    assert results[0]["id"] == "1"


def test_dim_mismatch_error_carries_metadata(store):
    """The exception must expose model_name, model_dim, collection_name,
    collection_dim attributes for programmatic recovery (e.g. an
    automated re-indexer that decides whether to drop + rebuild).
    """
    store.create_collection("docs", dimension=DIM)
    wrong_dim = DIM * 2
    docs = [VectorDocument(id="1", content="hello", vector=_vec_n(1.0, wrong_dim))]
    with pytest.raises(DimMismatchError) as excinfo:
        store.insert("docs", docs)
    err = excinfo.value
    assert err.collection_name == "docs"
    assert err.collection_dim == DIM
    assert err.model_dim == wrong_dim
    # Default model_name when caller doesn't supply one
    assert err.model_name == "<unknown>"


def test_validate_all_collections_raises_on_first_mismatch(store):
    """validate_all_collections walks every collection and raises on the
    first dim mismatch, useful for startup-time fail-fast.
    """
    store.create_collection("good", dimension=DIM)
    store.create_collection("bad", dimension=DIM * 2)
    with pytest.raises(DimMismatchError) as excinfo:
        store.validate_all_collections(DIM, model_name="test-model")
    err = excinfo.value
    assert err.model_name == "test-model"
    assert err.model_dim == DIM
    assert err.collection_dim == DIM * 2
    assert err.collection_name == "bad"


def test_validate_all_collections_passes_when_all_match(store):
    """validate_all_collections is a no-op when every collection has the
    expected dim.
    """
    store.create_collection("col-a", dimension=DIM)
    store.create_collection("col-b", dimension=DIM)
    store.validate_all_collections(DIM)  # must not raise


def test_dim_validation_skipped_for_legacy_collection_without_metadata(store):
    """Backward compat: collections without a 'dimension' metadata key
    (created by an older fleet-mem before the key was added) skip
    validation rather than raising.
    """
    # Simulate a legacy collection by creating one through the raw client
    # without the 'dimension' key. ChromaDB requires non-empty metadata for
    # the call but accepts arbitrary keys, so a single hnsw setting works.
    store._client.get_or_create_collection(
        name="legacy",
        metadata={"hnsw:space": "l2"},
    )
    # Insert with a dim — must NOT raise even though legacy collection
    # has no recorded dim to compare against.
    store.insert(
        "legacy",
        [VectorDocument(id="1", content="x", vector=_vec(1.0))],
    )
    # Search likewise must not raise.
    results = store.search("legacy", vector=_vec(1.0), limit=5)
    assert len(results) == 1


def test_dim_validation_cache_refreshed_on_drop_and_recreate(store):
    """If a collection is dropped and re-created with a different dim, the
    next insert/search must validate against the new dim, not the cached
    old one.
    """
    store.create_collection("docs", dimension=DIM)
    # Prime the cache by doing a successful insert at DIM
    store.insert("docs", [VectorDocument(id="1", content="x", vector=_vec(1.0))])
    store.drop_collection("docs")
    # Re-create at a different dim
    new_dim = DIM * 2
    store.create_collection("docs", dimension=new_dim)
    # Old DIM must now mismatch
    with pytest.raises(DimMismatchError):
        store.insert("docs", [VectorDocument(id="1", content="x", vector=_vec(1.0))])
    # New dim must succeed
    store.insert(
        "docs",
        [VectorDocument(id="1", content="x", vector=_vec_n(1.0, new_dim))],
    )
