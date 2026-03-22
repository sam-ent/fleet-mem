from unittest.mock import MagicMock, patch

import pytest

from fleet_mem.indexer import _chunk_id, index_codebase, index_files
from fleet_mem.splitter.ast_splitter import ASTChunk
from fleet_mem.splitter.text_splitter import TextChunk


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def mock_embedder():
    embedder = MagicMock()
    embedder.get_dimension.return_value = 128
    embedder.embed_batch.side_effect = lambda texts: [[0.1] * 128 for _ in texts]
    return embedder


def test_chunk_id_is_deterministic():
    id1 = _chunk_id("project", "test.py", 1, 10)
    id2 = _chunk_id("project", "test.py", 1, 10)
    id3 = _chunk_id("project", "test.py", 2, 10)
    assert id1 == id2
    assert id1 != id3
    assert isinstance(id1, str)


def test_index_files_success(mock_db, mock_embedder, tmp_path):
    root = tmp_path
    rel_path = "hello.py"
    abs_path = root / rel_path
    abs_path.write_text("def hello():\n    pass")

    with (
        patch("fleet_mem.indexer.supported_languages", return_value=["python"]),
        patch("fleet_mem.indexer.split_ast") as mock_split,
    ):
        mock_split.return_value = [
            ASTChunk(
                content="def hello():\n    pass",
                start_line=1,
                end_line=2,
                chunk_type="function",
                name="hello",
            )
        ]

        result = index_files(root, "myproj", [rel_path], mock_db, mock_embedder)

        assert result.chunks_inserted == 1
        assert result.files_succeeded == 1
        assert result.files_failed == 0
        assert len(result.errors) == 0

        mock_db.create_collection.assert_called_once_with("code_myproj", 128)
        mock_db.insert.assert_called_once()
        _, args = mock_db.insert.call_args
        collection, docs = mock_db.insert.call_args[0]
        assert collection == "code_myproj"
        assert len(docs) == 1
        assert docs[0].metadata["name"] == "hello"
        assert docs[0].metadata["file_path"] == rel_path


def test_index_files_missing_and_failed(mock_db, mock_embedder, tmp_path):
    root = tmp_path
    # One missing file, one that raises exception
    f1 = "missing.py"
    f2 = "error.py"
    (root / f2).write_text("content")

    with patch("fleet_mem.indexer._split_file", side_effect=Exception("split failed")):
        result = index_files(root, "myproj", [f1, f2], mock_db, mock_embedder)

    assert result.files_failed == 2
    assert result.errors[f1] == "file not found"
    assert "split failed" in result.errors[f2]
    assert result.chunks_inserted == 0


def test_index_files_batching(mock_db, mock_embedder, tmp_path):
    root = tmp_path
    rel_path = "large.py"
    (root / rel_path).write_text("content")

    # Create 70 chunks (batch size is 64)
    chunks = [
        TextChunk(content=f"c{i}", start_line=i, end_line=i, chunk_type="text") for i in range(70)
    ]

    with patch("fleet_mem.indexer._split_file", return_value=chunks):
        result = index_files(root, "myproj", [rel_path], mock_db, mock_embedder)

    assert result.chunks_inserted == 70
    assert mock_embedder.embed_batch.call_count == 2
    assert mock_db.insert.call_count == 2


def test_index_codebase_success(mock_db, mock_embedder, tmp_path):
    root = tmp_path
    progress_mock = MagicMock()

    # Mock scan_files to return 2 files
    files = [(root / "a.py", "python", "content a"), (root / "b.py", "python", "content b")]

    with (
        patch("fleet_mem.indexer.scan_files", return_value=files),
        patch("fleet_mem.indexer._split_file") as mock_split,
    ):
        mock_split.return_value = [
            TextChunk(content="chunk", start_line=1, end_line=1, chunk_type="text"),
        ]

        count = index_codebase(root, "proj", mock_db, mock_embedder, progress=progress_mock)

        assert count == 2
        assert progress_mock.call_count > 0
        # Phase 1: Splitting, Phase 2: Embedding, Phase 3: Inserting
        # For 2 docs, each phase runs once.
        mock_db.insert.assert_called_once()


def test_index_codebase_no_files(mock_db, mock_embedder, tmp_path):
    root = tmp_path
    with patch("fleet_mem.indexer.scan_files", return_value=[]):
        count = index_codebase(root, "proj", mock_db, mock_embedder)
        assert count == 0
        mock_db.insert.assert_not_called()


def test_fallback_to_text_splitter(mock_db, mock_embedder, tmp_path):
    root = tmp_path
    rel_path = "other.txt"
    (root / rel_path).write_text("plain text")

    with (
        patch("fleet_mem.indexer.supported_languages", return_value=["python"]),
        patch("fleet_mem.indexer.split_text") as mock_text_split,
    ):
        mock_text_split.return_value = [
            TextChunk(content="plain text", start_line=1, end_line=1, chunk_type="text"),
        ]

        result = index_files(root, "myproj", [rel_path], mock_db, mock_embedder)

        assert result.chunks_inserted == 1
        mock_text_split.assert_called_once_with("plain text")


def test_index_files_empty_list(mock_db, mock_embedder, tmp_path):
    result = index_files(tmp_path, "proj", [], mock_db, mock_embedder)
    assert result.chunks_inserted == 0
    assert result.files_succeeded == 0
    mock_db.insert.assert_not_called()
