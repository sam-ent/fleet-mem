import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest
import xxhash

from fleet_mem.memory.embedder import (
    MemoryEmbedder,
    MemoryResult,
    StaleAnchor,
    _hash_file,
    MEMORY_COLLECTION,
)
from fleet_mem.vectordb.types import VectorDocument


@pytest.fixture
def mock_engine():
    return MagicMock()


@pytest.fixture
def mock_embedding():
    mock = MagicMock()
    mock.get_dimension.return_value = 384
    mock.embed.return_value = [0.1] * 384
    return mock


@pytest.fixture
def mock_vectordb():
    mock = MagicMock()
    mock.has_collection.return_value = True
    return mock


@pytest.fixture
def embedder(mock_engine, mock_embedding, mock_vectordb):
    return MemoryEmbedder(mock_engine, mock_embedding, mock_vectordb)


def test_init_creates_collection_if_missing(mock_engine, mock_embedding, mock_vectordb):
    mock_vectordb.has_collection.return_value = False
    MemoryEmbedder(mock_engine, mock_embedding, mock_vectordb)
    mock_vectordb.create_collection.assert_called_once_with(MEMORY_COLLECTION, dimension=384)


def test_memory_store_basic(embedder, mock_engine, mock_embedding, mock_vectordb):
    node_id = embedder.memory_store(
        node_type="thought",
        content="test content",
        summary="test summary",
        keywords=["test", "memory"]
    )

    assert isinstance(node_id, str)
    mock_engine.insert_node.assert_called_once()
    mock_embedding.embed.assert_called_once_with("test content")
    mock_vectordb.insert.assert_called_once()
    
    # Verify VectorDocument construction
    args, _ = mock_vectordb.insert.call_args
    assert args[0] == MEMORY_COLLECTION
    assert isinstance(args[1][0], VectorDocument)
    assert args[1][0].id == node_id


def test_memory_store_with_file_anchor(embedder, mock_engine):
    with patch("fleet_mem.memory.embedder._hash_file") as mock_hash:
        mock_hash.return_value = "fakehash"
        embedder.memory_store(
            node_type="code",
            content="print(1)",
            file_path="test.py",
            line_range="10-20"
        )
        
        mock_engine.insert_file_anchor.assert_called_once()
        args = mock_engine.insert_file_anchor.call_args[1]
        assert args["file_path"] == "test.py"
        assert args["file_hash"] == "fakehash"
        assert args["line_start"] == 10
        assert args["line_end"] == 20


def test_memory_store_notifies_subscribers(embedder, mock_engine):
    with patch("fleet_mem.fleet.cross_agent._notify_subscribers") as mock_notify:
        with patch("fleet_mem.memory.embedder._hash_file"):
            embedder.memory_store(
                node_type="info",
                content="important",
                file_path="src/app.py",
                agent_id="agent-1",
                fleet_db_path="/tmp/fleet.db",
                project_path="/home/user/project"
            )
            mock_notify.assert_called_once()


def test_memory_search_hybrid_fusion(embedder, mock_engine, mock_vectordb):
    # Mock FTS results (ID 1 is rank 1, ID 2 is rank 2)
    mock_engine.search_fts.return_value = [{"id": "1"}, {"id": "2"}]
    
    # Mock VectorDB results (ID 2 is rank 1, ID 3 is rank 2)
    mock_vectordb.search.return_value = [{"id": "2"}, {"id": "3"}]
    
    # Mock node retrieval
    nodes = {
        "1": {"node_type": "a", "content": "c1", "summary": "s1", "file_path": "f1"},
        "2": {"node_type": "a", "content": "c2", "summary": "s2", "file_path": "f2"},
        "3": {"node_type": "a", "content": "c3", "summary": "s3", "file_path": "f3"},
    }
    mock_engine.get_node.side_effect = lambda nid: nodes.get(nid)

    results = embedder.memory_search("query", top_k=5)

    # RRF check:
    # ID 1: 1/1 (fts) + 0 (semantic) = 1.0
    # ID 2: 1/2 (fts) + 1/1 (semantic) = 1.5
    # ID 3: 0 (fts) + 1/2 (semantic) = 0.5
    # Expected order: 2, 1, 3
    assert [r.id for r in results] == ["2", "1", "3"]
    assert results[0].score == 1.5


def test_memory_search_node_type_filtering(embedder, mock_engine, mock_vectordb):
    mock_engine.search_fts.return_value = [{"id": "1"}]
    mock_vectordb.search.return_value = []
    mock_engine.get_node.return_value = {
        "node_type": "mismatch", "content": "...", "summary": "...", "file_path": None
    }

    results = embedder.memory_search("query", node_type="target")
    assert len(results) == 0


def test_memory_promote(embedder, mock_engine):
    embedder.memory_promote("id123", "/global/scope")
    mock_engine.update_node_project_path.assert_called_once_with("id123", "/global/scope")


def test_stale_check_detects_changes(embedder, mock_engine):
    mock_engine.get_all_file_anchors.return_value = [
        {"id": "a1", "memory_id": "m1", "file_path": "changed.txt", "file_hash": "old"},
        {"id": "a2", "memory_id": "m2", "file_path": "same.txt", "file_hash": "current"},
    ]
    
    def side_effect(path):
        if path == "changed.txt": return "new"
        if path == "same.txt": return "current"
        raise FileNotFoundError()

    with patch("fleet_mem.memory.embedder._hash_file", side_effect=side_effect):
        stale = embedder.stale_check()
        
        assert len(stale) == 1
        assert stale[0].anchor_id == "a1"
        assert stale[0].current_hash == "new"


def test_stale_check_handles_missing_files(embedder, mock_engine):
    mock_engine.get_all_file_anchors.return_value = [
        {"id": "a1", "memory_id": "m1", "file_path": "missing.txt", "file_hash": "hash"},
    ]
    
    with patch("fleet_mem.memory.embedder._hash_file", side_effect=FileNotFoundError):
        stale = embedder.stale_check()
        assert len(stale) == 1
        assert stale[0].current_hash == "<missing>"


def test_hash_file_utility(tmp_path):
    f = tmp_path / "test.bin"
    content = b"hello world" * 1000
    f.write_bytes(content)
    
    expected = xxhash.xxh3_64(content).hexdigest()
    assert _hash_file(str(f)) == expected


def test_hash_file_chunking(tmp_path):
    # Test that large files are read correctly in chunks
    f = tmp_path / "large.bin"
    # Create a 10KB file to trigger multiple 8KB reads
    content = b"A" * 10240 
    f.write_bytes(content)
    
    expected = xxhash.xxh3_64(content).hexdigest()
    assert _hash_file(str(f)) == expected
