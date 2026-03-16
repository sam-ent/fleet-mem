"""Tests for memory engine and embedder."""

import hashlib

import pytest

from src.memory.embedder import MemoryEmbedder, _sha1_file
from src.memory.engine import MemoryEngine
from src.vectordb.chromadb_store import ChromaDBStore

DIM = 8


def _vec(seed: float) -> list[float]:
    return [seed * (i + 1) * 0.1 for i in range(DIM)]


class FakeEmbedding:
    """Deterministic embedding for tests."""

    def __init__(self):
        self._call_count = 0

    def embed(self, text: str) -> list[float]:
        # Hash text to produce a deterministic vector
        h = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(h >> (i * 4) & 0xF) / 15.0 for i in range(DIM)]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def get_dimension(self) -> int:
        return DIM

    def get_provider(self) -> str:
        return "fake/test"


@pytest.fixture
def engine(tmp_path):
    eng = MemoryEngine(tmp_path / "test.db")
    eng.open()
    yield eng
    eng.close()


@pytest.fixture
def embedder(tmp_path, engine):
    chroma = ChromaDBStore(path=tmp_path / "chroma")
    embed = FakeEmbedding()
    return MemoryEmbedder(engine=engine, embedding=embed, vectordb=chroma)


class TestMemoryEngine:
    def test_context_manager(self, tmp_path):
        db_path = tmp_path / "ctx.db"
        with MemoryEngine(db_path) as eng:
            assert eng.conn is not None
        # After exit, conn should be None
        assert eng._conn is None

    def test_insert_and_get_node(self, engine):
        engine.insert_node(
            node_id="abc123",
            node_type="pattern",
            content="Use context managers for DB access",
            summary="DB pattern",
        )
        row = engine.get_node("abc123")
        assert row is not None
        assert row["node_type"] == "pattern"
        assert row["content"] == "Use context managers for DB access"
        assert row["summary"] == "DB pattern"

    def test_get_missing_node_returns_none(self, engine):
        assert engine.get_node("nonexistent") is None

    def test_fts_search(self, engine):
        engine.insert_node(node_id="n1", node_type="note", content="python asyncio patterns")
        engine.insert_node(node_id="n2", node_type="note", content="javascript promises")
        results = engine.search_fts("python")
        ids = [r["id"] for r in results]
        assert "n1" in ids

    def test_file_anchor_insert(self, engine):
        engine.insert_node(node_id="m1", node_type="code", content="some code")
        engine.insert_file_anchor(
            anchor_id="a1",
            memory_id="m1",
            file_path="/tmp/test.py",
            file_hash="abc123",
            line_start=10,
            line_end=20,
        )
        anchors = engine.get_all_file_anchors()
        assert len(anchors) == 1
        assert anchors[0]["file_path"] == "/tmp/test.py"


class TestMemoryStoreRoundTrip:
    def test_store_and_retrieve(self, embedder, engine):
        node_id = embedder.memory_store(
            node_type="pattern",
            content="Always use type hints in Python functions",
            summary="Type hint pattern",
            keywords=["python", "typing"],
            source="agent",
        )
        row = engine.get_node(node_id)
        assert row is not None
        assert row["node_type"] == "pattern"
        assert row["keywords"] == "python,typing"

    def test_store_with_file_creates_anchor(self, embedder, engine, tmp_path):
        test_file = tmp_path / "sample.py"
        test_file.write_text("print('hello')")

        node_id = embedder.memory_store(
            node_type="code",
            content="hello world printer",
            file_path=str(test_file),
            line_range="1-1",
        )
        anchors = engine.get_all_file_anchors()
        assert len(anchors) == 1
        assert anchors[0]["memory_id"] == node_id
        assert anchors[0]["file_hash"] == _sha1_file(str(test_file))


class TestStaleCheck:
    def test_unchanged_file_not_stale(self, embedder, tmp_path):
        test_file = tmp_path / "stable.py"
        test_file.write_text("x = 1")

        embedder.memory_store(
            node_type="code",
            content="variable assignment",
            file_path=str(test_file),
        )
        stale = embedder.stale_check()
        assert len(stale) == 0

    def test_changed_file_is_stale(self, embedder, tmp_path):
        test_file = tmp_path / "changing.py"
        test_file.write_text("x = 1")

        embedder.memory_store(
            node_type="code",
            content="variable assignment",
            file_path=str(test_file),
        )
        # Modify the file
        test_file.write_text("x = 2")

        stale = embedder.stale_check()
        assert len(stale) == 1
        assert stale[0].file_path == str(test_file)
        assert stale[0].current_hash != stale[0].stored_hash

    def test_missing_file_is_stale(self, embedder, tmp_path):
        test_file = tmp_path / "ephemeral.py"
        test_file.write_text("temp")

        embedder.memory_store(
            node_type="code",
            content="temp file",
            file_path=str(test_file),
        )
        test_file.unlink()

        stale = embedder.stale_check()
        assert len(stale) == 1
        assert stale[0].current_hash == "<missing>"


class TestHybridSearch:
    def test_search_returns_results(self, embedder):
        embedder.memory_store(node_type="pattern", content="python asyncio event loop patterns")
        embedder.memory_store(node_type="note", content="javascript callback patterns")
        embedder.memory_store(node_type="pattern", content="rust ownership and borrowing")

        results = embedder.memory_search("python asyncio", top_k=5)
        assert len(results) > 0
        # The python asyncio result should rank high
        ids_content = [r.content for r in results]
        assert any("asyncio" in c for c in ids_content)

    def test_search_with_node_type_filter(self, embedder):
        embedder.memory_store(node_type="pattern", content="python typing best practices")
        embedder.memory_store(node_type="note", content="python typing notes")

        results = embedder.memory_search("python typing", node_type="pattern")
        for r in results:
            assert r.node_type == "pattern"

    def test_search_empty_db(self, embedder):
        results = embedder.memory_search("anything")
        assert results == []


class TestMemoryPromote:
    def test_promote_clears_project_path(self, embedder, engine):
        node_id = embedder.memory_store(
            node_type="pattern",
            content="project-specific pattern",
            project_path="/home/user/project",
        )
        row = engine.get_node(node_id)
        assert row["project_path"] == "/home/user/project"

        embedder.memory_promote(node_id, target_scope=None)
        row = engine.get_node(node_id)
        assert row["project_path"] is None
