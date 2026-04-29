"""Indexing orchestrator: scan -> split -> embed -> insert into ChromaDB."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import structlog
import xxhash

from fleet_mem.config import Config
from fleet_mem.embedding.base import Embedding
from fleet_mem.splitter.ast_splitter import ASTChunk, split_ast, supported_languages
from fleet_mem.splitter.file_scanner import scan_files
from fleet_mem.splitter.text_splitter import TextChunk, split_text
from fleet_mem.vectordb.base import VectorDatabase
from fleet_mem.vectordb.types import VectorDocument

logger = structlog.get_logger(__name__)

# Batch size for embedding calls
_EMBED_BATCH_SIZE = 64


def _count_tokens(tokenizer: Any, text: str) -> int:
    """Count tokens using a HF ``tokenizers.Tokenizer``-like object.

    Kept small + duck-typed so the indexer does not have to import the
    optional ``tokenizers`` package directly. Any object with an
    ``encode(text).ids`` shape works; this matches the HF ``Tokenizer``
    API plus easy-to-build test fakes.
    """
    return len(tokenizer.encode(text).ids)


def _exceeds_cap(
    content: str,
    max_chars: int,
    tokenizer: Any | None,
    max_tokens: int | None,
) -> bool:
    """Return True if ``content`` violates either the char- or token-cap.

    The token-cap is consulted only when ``tokenizer`` is non-None AND
    ``max_tokens`` is a positive int — otherwise the char-cap stands
    alone (preserving pre-#42 behavior). When both caps apply, both must
    be satisfied (token-cap is typically the stricter bound for dense
    content; char-cap protects against pathological tokenizer behavior).
    """
    if isinstance(max_chars, int) and max_chars > 0 and len(content) > max_chars:
        return True
    if (
        tokenizer is not None
        and isinstance(max_tokens, int)
        and max_tokens > 0
        and _count_tokens(tokenizer, content) > max_tokens
    ):
        return True
    return False


def _cap_chunk_sizes(
    chunks: list[ASTChunk | TextChunk],
    max_chars: int,
    tokenizer: Any | None = None,
    max_tokens: int | None = None,
) -> list[ASTChunk | TextChunk]:
    """Ensure no chunk exceeds ``max_chars`` characters (and optionally
    ``max_tokens`` tokens for the supplied tokenizer — issue #42).

    Oversized chunks (typically large AST definitions or whole-file
    fallback chunks for unsupported languages) are recursively subdivided
    at the nearest newline boundary, falling back to the midpoint.
    Line-number metadata is approximated by counting newlines in the
    prefix; it may be slightly coarser than the original splitter
    produces but remains monotonic and within the chunk's line range.

    This is a safety net applied *after* the language-aware splitters so
    that no individual chunk overruns the embed model's context window.
    When ``tokenizer`` and ``max_tokens`` are supplied, splitting also
    enforces the token-count bound — necessary for dense content (code
    with many symbols, non-English text, base64-like strings) where the
    char-cap alone underestimates token count.
    """
    chars_active = isinstance(max_chars, int) and max_chars > 0
    tokens_active = tokenizer is not None and isinstance(max_tokens, int) and max_tokens > 0
    if not chars_active and not tokens_active:
        return chunks

    result: list[ASTChunk | TextChunk] = []
    for chunk in chunks:
        if not _exceeds_cap(chunk.content, max_chars, tokenizer, max_tokens):
            result.append(chunk)
            continue
        result.extend(_split_oversized(chunk, max_chars, tokenizer, max_tokens))
    return result


def _split_oversized(
    chunk: ASTChunk | TextChunk,
    max_chars: int,
    tokenizer: Any | None = None,
    max_tokens: int | None = None,
) -> list[ASTChunk | TextChunk]:
    """Recursively split ``chunk`` so every piece is <= ``max_chars`` (and
    <= ``max_tokens`` tokens when token-aware capping is enabled).

    Prefers splitting on the newline nearest the midpoint; falls back
    to a hard midpoint split for pathological single-line input.
    """
    content = chunk.content
    if not _exceeds_cap(content, max_chars, tokenizer, max_tokens):
        return [chunk]

    # Pick a split point: newline closest to the midpoint, else midpoint.
    mid = len(content) // 2
    left_nl = content.rfind("\n", 0, mid)
    right_nl = content.find("\n", mid)
    if left_nl == -1 and right_nl == -1:
        split_at = mid
    elif left_nl == -1:
        split_at = right_nl + 1
    elif right_nl == -1:
        split_at = left_nl + 1
    else:
        # pick whichever is nearer to mid
        split_at = (left_nl + 1) if (mid - left_nl) <= (right_nl - mid) else (right_nl + 1)

    if split_at <= 0 or split_at >= len(content):
        split_at = mid

    left_content = content[:split_at]
    right_content = content[split_at:]

    # Approximate line ranges by counting newlines in the prefix.
    left_newlines = left_content.count("\n")
    left_start_line = chunk.start_line
    left_end_line = min(chunk.end_line, chunk.start_line + left_newlines)
    right_start_line = left_end_line
    right_end_line = chunk.end_line

    def _rebuild(content: str, start_line: int, end_line: int) -> ASTChunk | TextChunk:
        if isinstance(chunk, ASTChunk):
            return ASTChunk(
                content=content,
                start_line=start_line,
                end_line=end_line,
                chunk_type=chunk.chunk_type,
                name=chunk.name,
                parent_name=chunk.parent_name,
            )
        return TextChunk(
            content=content,
            start_line=start_line,
            end_line=end_line,
            chunk_type=chunk.chunk_type,
        )

    left = _rebuild(left_content, left_start_line, left_end_line)
    right = _rebuild(right_content, right_start_line, right_end_line)

    return _split_oversized(left, max_chars, tokenizer, max_tokens) + _split_oversized(
        right, max_chars, tokenizer, max_tokens
    )


ProgressCallback = Callable[[int, int, str], None]  # (current, total, message)


def _chunk_id(project_name: str, file_path: str, start_line: int, end_line: int) -> str:
    """Generate a deterministic chunk ID."""
    raw = f"{project_name}:{file_path}:{start_line}-{end_line}"
    return xxhash.xxh3_64(raw.encode()).hexdigest()


def _split_file(
    content: str,
    language: str,
    ast_languages: set[str],
    max_chunk_chars: int | None = None,
    *,
    tokenizer: Any | None = None,
    max_chunk_tokens: int | None = None,
) -> list[ASTChunk | TextChunk]:
    """Split a file using AST or text splitter.

    If ``max_chunk_chars`` is provided and > 0, any chunk whose
    ``content`` exceeds the cap is recursively subdivided so the
    downstream embed call cannot exceed the model's context window.

    When ``tokenizer`` and ``max_chunk_tokens`` are supplied (issue #42),
    splitting also enforces the token-count bound. Both caps are applied
    simultaneously: a chunk is subdivided until it satisfies whichever
    cap(s) are configured. ``tokenizer`` should be a HF
    ``tokenizers.Tokenizer``-like object (must support
    ``.encode(text).ids``); pass ``None`` to disable the token-aware path.
    """
    if language in ast_languages:
        chunks = split_ast(content, language)
        if not chunks:
            chunks = split_text(content)
    else:
        # Fallback to text splitter
        chunks = split_text(content)

    chars_capped = isinstance(max_chunk_chars, int) and max_chunk_chars > 0
    tokens_capped = (
        tokenizer is not None and isinstance(max_chunk_tokens, int) and max_chunk_tokens > 0
    )
    if chars_capped or tokens_capped:
        chunks = _cap_chunk_sizes(
            chunks,
            max_chunk_chars or 0,
            tokenizer=tokenizer if tokens_capped else None,
            max_tokens=max_chunk_tokens if tokens_capped else None,
        )
    return chunks


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
    *,
    config: Config | None = None,
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

    cfg = config or Config()
    max_chunk_chars = cfg.max_chunk_chars
    max_chunk_tokens = cfg.max_chunk_tokens
    # Lazy: only ask the embedder for a tokenizer when token-aware capping
    # is actually configured. Keeps the char-cap-only path side-effect-free.
    tokenizer = (
        embedder.get_tokenizer()
        if isinstance(max_chunk_tokens, int) and max_chunk_tokens > 0
        else None
    )
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

            chunks = _split_file(
                content,
                language,
                ast_langs,
                max_chunk_chars,
                tokenizer=tokenizer,
                max_chunk_tokens=max_chunk_tokens,
            )

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
    config: Config | None = None,
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

    cfg = config or Config()
    max_chunk_chars = cfg.max_chunk_chars
    max_chunk_tokens = cfg.max_chunk_tokens
    tokenizer = (
        embedder.get_tokenizer()
        if isinstance(max_chunk_tokens, int) and max_chunk_tokens > 0
        else None
    )
    ast_langs = set(supported_languages())

    # Phase 1: Scan and split
    all_docs: list[VectorDocument] = []
    files = list(scan_files(root, extra_ignore_patterns=extra_ignore_patterns))
    total_files = len(files)

    for idx, (file_path, language, content) in enumerate(files):
        if progress:
            progress(idx + 1, total_files, f"Splitting {file_path.name}")

        rel_path = str(file_path.relative_to(root.resolve()))
        chunks = _split_file(
            content,
            language,
            ast_langs,
            max_chunk_chars,
            tokenizer=tokenizer,
            max_chunk_tokens=max_chunk_tokens,
        )

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
