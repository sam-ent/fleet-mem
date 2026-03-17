"""Indexing orchestrator: scan -> split -> embed -> insert into ChromaDB."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import xxhash

from fleet_mem.embedding.base import Embedding
from fleet_mem.splitter.ast_splitter import ASTChunk, split_ast, supported_languages
from fleet_mem.splitter.file_scanner import scan_files
from fleet_mem.splitter.text_splitter import TextChunk, split_text
from fleet_mem.vectordb.base import VectorDatabase
from fleet_mem.vectordb.types import VectorDocument

logger = logging.getLogger(__name__)

# Batch size for embedding calls
_EMBED_BATCH_SIZE = 64

ProgressCallback = Callable[[int, int, str], None]  # (current, total, message)


def _chunk_id(project_name: str, file_path: str, start_line: int, end_line: int) -> str:
    """Generate a deterministic chunk ID."""
    raw = f"{project_name}:{file_path}:{start_line}-{end_line}"
    return xxhash.xxh3_64(raw.encode()).hexdigest()


def _split_file(
    content: str,
    language: str,
    ast_languages: set[str],
) -> list[ASTChunk | TextChunk]:
    """Split a file using AST or text splitter."""
    if language in ast_languages:
        chunks = split_ast(content, language)
        if chunks:
            return chunks
    # Fallback to text splitter
    return split_text(content)


@dataclass
class IndexFilesResult:
    """Result of indexing specific files."""

    chunks_inserted: int
    files_succeeded: int
    files_failed: int
    errors: dict[str, str]  # file_path -> error message


def index_files(
    root: Path,
    project_name: str,
    file_paths: list[str],
    db: VectorDatabase,
    embedder: Embedding,
) -> IndexFilesResult:
    """Index specific files into ChromaDB.

    Unlike ``index_codebase`` which walks an entire directory, this function
    processes only the given *file_paths* (relative to *root*). Each file is
    handled independently — a failure in one file does not prevent the rest
    from being indexed.

    Args:
        root: Root directory of the codebase.
        project_name: Name used for the ChromaDB collection (``code_{project_name}``).
        file_paths: Relative file paths (relative to *root*) to index.
        db: Vector database instance.
        embedder: Embedding provider.

    Returns:
        An ``IndexFilesResult`` with counts of inserted chunks and per-file errors.
    """
    collection_name = f"code_{project_name}"
    dimension = embedder.get_dimension()
    db.create_collection(collection_name, dimension)

    ast_langs = set(supported_languages())
    all_docs: list[VectorDocument] = []
    files_succeeded = 0
    files_failed = 0
    errors: dict[str, str] = {}

    for rel_path in file_paths:
        try:
            abs_path = root / rel_path
            if not abs_path.is_file():
                logger.warning("index_files: skipping missing file %s", rel_path)
                files_failed += 1
                errors[rel_path] = "file not found"
                continue

            content = abs_path.read_text(errors="replace")
            suffix = abs_path.suffix.lower()
            from .splitter.file_scanner import EXTENSION_MAP

            language = EXTENSION_MAP.get(suffix, "unknown")

            chunks = _split_file(content, language, ast_langs)

            for chunk in chunks:
                metadata = {
                    "file_path": rel_path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "language": language,
                    "chunk_type": chunk.chunk_type,
                    "project_name": project_name,
                }
                if isinstance(chunk, ASTChunk) and chunk.name:
                    metadata["name"] = chunk.name

                content_with_context = (
                    f"# {rel_path} (L{chunk.start_line}-L{chunk.end_line})\n{chunk.content}"
                )
                doc_id = _chunk_id(project_name, rel_path, chunk.start_line, chunk.end_line)
                all_docs.append(
                    VectorDocument(id=doc_id, content=content_with_context, metadata=metadata)
                )

            files_succeeded += 1
        except Exception as exc:
            logger.warning("index_files: failed to process %s: %s", rel_path, exc)
            files_failed += 1
            errors[rel_path] = str(exc)

    if not all_docs:
        return IndexFilesResult(
            chunks_inserted=0,
            files_succeeded=files_succeeded,
            files_failed=files_failed,
            errors=errors,
        )

    # Embed in batches
    for batch_start in range(0, len(all_docs), _EMBED_BATCH_SIZE):
        batch = all_docs[batch_start : batch_start + _EMBED_BATCH_SIZE]
        texts = [d.content for d in batch]
        vectors = embedder.embed_batch(texts)
        for doc, vec in zip(batch, vectors):
            doc.vector = vec

    # Insert in batches
    for batch_start in range(0, len(all_docs), _EMBED_BATCH_SIZE):
        batch = all_docs[batch_start : batch_start + _EMBED_BATCH_SIZE]
        db.insert(collection_name, batch)

    return IndexFilesResult(
        chunks_inserted=len(all_docs),
        files_succeeded=files_succeeded,
        files_failed=files_failed,
        errors=errors,
    )


def index_codebase(
    root: Path,
    project_name: str,
    db: VectorDatabase,
    embedder: Embedding,
    *,
    progress: ProgressCallback | None = None,
    extra_ignore_patterns: list[str] | None = None,
) -> int:
    """Index a codebase into ChromaDB.

    Scans files, splits them into chunks, embeds, and inserts into a
    collection named ``code_{project_name}``.

    Args:
        root: Root directory of the codebase.
        project_name: Name used for the ChromaDB collection (``code_{project_name}``).
        db: Vector database instance.
        embedder: Embedding provider.
        progress: Optional callback ``(current_file, total_files, message)``.
        extra_ignore_patterns: Additional gitignore-style patterns.

    Returns:
        Number of chunks indexed.
    """
    collection_name = f"code_{project_name}"
    dimension = embedder.get_dimension()
    db.create_collection(collection_name, dimension)

    ast_langs = set(supported_languages())

    # Phase 1: Scan and split
    all_docs: list[VectorDocument] = []
    files = list(scan_files(root, extra_ignore_patterns=extra_ignore_patterns))
    total_files = len(files)

    for idx, (file_path, language, content) in enumerate(files):
        if progress:
            progress(idx + 1, total_files, f"Splitting {file_path.name}")

        rel_path = str(file_path.relative_to(root.resolve()))
        chunks = _split_file(content, language, ast_langs)

        for chunk in chunks:
            metadata = {
                "file_path": rel_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "language": language,
                "chunk_type": chunk.chunk_type,
                "project_name": project_name,
            }
            if isinstance(chunk, ASTChunk) and chunk.name:
                metadata["name"] = chunk.name

            # Prepend file path context to chunk content for better retrieval
            content_with_context = (
                f"# {rel_path} (L{chunk.start_line}-L{chunk.end_line})\n{chunk.content}"
            )

            doc_id = _chunk_id(project_name, rel_path, chunk.start_line, chunk.end_line)
            all_docs.append(
                VectorDocument(
                    id=doc_id,
                    content=content_with_context,
                    metadata=metadata,
                )
            )

    if not all_docs:
        return 0

    # Phase 2: Embed in batches
    total_docs = len(all_docs)
    for batch_start in range(0, total_docs, _EMBED_BATCH_SIZE):
        batch_end = min(batch_start + _EMBED_BATCH_SIZE, total_docs)
        batch = all_docs[batch_start:batch_end]

        if progress:
            progress(
                batch_start + len(batch),
                total_docs,
                f"Embedding chunks {batch_start + 1}-{batch_end}",
            )

        texts = [d.content for d in batch]
        vectors = embedder.embed_batch(texts)
        for doc, vec in zip(batch, vectors):
            doc.vector = vec

    # Phase 3: Insert in batches
    for batch_start in range(0, total_docs, _EMBED_BATCH_SIZE):
        batch_end = min(batch_start + _EMBED_BATCH_SIZE, total_docs)
        batch = all_docs[batch_start:batch_end]

        if progress:
            progress(
                batch_start + len(batch),
                total_docs,
                f"Inserting chunks {batch_start + 1}-{batch_end}",
            )

        db.insert(collection_name, batch)

    return total_docs
