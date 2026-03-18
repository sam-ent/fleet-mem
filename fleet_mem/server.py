"""Fleet-Mem MCP Server — entry point.

Provides semantic code search and agent memory tools via MCP protocol.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from .observability import configure_logging, get_tracer, hash_content

configure_logging()
logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Index status tracking
# ---------------------------------------------------------------------------

_index_status: dict[str, dict[str, Any]] = {}
# project_name -> {status, file_count, chunk_count, last_sync, error}

_status_lock = threading.Lock()


def _project_name_from_path(path: str) -> str:
    return Path(path).resolve().name


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

mcp = FastMCP("fleet-mem")


def _get_config():
    from .config import Config

    return Config()


def _get_db(config=None):
    from .vectordb.chromadb_store import ChromaDBStore

    cfg = config or _get_config()
    return ChromaDBStore(cfg.chroma_path)


def _get_embedder(config=None):
    from .embedding.cache import CachedEmbedding, EmbeddingCache

    cfg = config or _get_config()
    if cfg.embedding_provider == "openai-compat":
        from .embedding.openai_compat import OpenAICompatibleEmbedding

        inner = OpenAICompatibleEmbedding(
            api_key=cfg.embed_api_key,
            base_url=cfg.embed_base_url,
            model=cfg.embed_model or None,
        )
    else:
        from .embedding.ollama_embed import OllamaEmbedding

        inner = OllamaEmbedding(cfg)

    cache = EmbeddingCache(cfg.embed_cache_path)
    return CachedEmbedding(inner=inner, cache=cache)


def _get_memory(config=None):
    from .memory.embedder import MemoryEmbedder
    from .memory.engine import MemoryEngine

    cfg = config or _get_config()
    engine = MemoryEngine(cfg.memory_db_path)
    engine.open()
    db = _get_db(cfg)
    embedder = _get_embedder(cfg)
    return MemoryEmbedder(engine, embedder, db)


# ---------------------------------------------------------------------------
# Tool: index_codebase
# ---------------------------------------------------------------------------


@mcp.tool(description="Index a codebase for semantic search. Runs in background.")
async def index_codebase(
    path: str,
    force: bool = False,
    extensions: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    """Start background indexing of a codebase directory.

    When *branch* is set, only files that differ from main are indexed into
    an overlay collection ``code_{project}__{branch}``.
    """
    from .indexer import index_codebase as _index

    tracer = get_tracer()
    span = tracer.start_span("fleet.index")
    project = _project_name_from_path(path)
    span.set_attribute("fleet.project", project)
    if branch:
        span.set_attribute("fleet.branch", branch)
    collection_name = f"code_{project}"
    config = _get_config()
    db = _get_db(config)
    embedder = _get_embedder(config)

    # Branch-aware indexing: delegate to BranchIndex
    if branch:
        from .fleet.branch_index import BranchIndex

        bi = BranchIndex(db, project)
        changed_files = bi.get_changed_files(path, branch)
        if not changed_files:
            span.set_attribute("fleet.chunk_count", 0)
            span.end()
            return {"project": project, "branch": branch, "status": "no_changes", "chunk_count": 0}

        with _status_lock:
            _index_status[f"{project}:{branch}"] = {
                "status": "indexing",
                "file_count": 0,
                "chunk_count": 0,
                "last_sync": None,
                "error": None,
            }

        def _run_branch():
            try:
                import xxhash

                from .splitter.ast_splitter import split_ast, supported_languages
                from .splitter.file_scanner import scan_files
                from .splitter.text_splitter import split_text
                from .vectordb.types import VectorDocument

                ast_langs = set(supported_languages())
                root = Path(path).resolve()
                changed_set = set(changed_files)

                all_docs: list[VectorDocument] = []
                files_iter = scan_files(root, extra_ignore_patterns=ignore_patterns)
                for file_path, language, content in files_iter:
                    rel = str(file_path.relative_to(root))
                    if rel not in changed_set:
                        continue
                    if language in ast_langs:
                        chunks = split_ast(content, language)
                    else:
                        chunks = []
                    if not chunks:
                        chunks = split_text(content)
                    for chunk in chunks:
                        meta = {
                            "file_path": rel,
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                            "language": language,
                            "chunk_type": chunk.chunk_type,
                            "project_name": project,
                        }
                        if hasattr(chunk, "name") and chunk.name:
                            meta["name"] = chunk.name
                        text = f"# {rel} (L{chunk.start_line}-L{chunk.end_line})\n{chunk.content}"
                        raw = f"{project}:{rel}:{chunk.start_line}-{chunk.end_line}"
                        doc_id = xxhash.xxh3_64(raw.encode()).hexdigest()
                        all_docs.append(VectorDocument(id=doc_id, content=text, metadata=meta))

                # Embed
                for i in range(0, len(all_docs), 64):
                    batch = all_docs[i : i + 64]
                    vecs = embedder.embed_batch([d.content for d in batch])
                    for d, v in zip(batch, vecs):
                        d.vector = v

                count = bi.index_branch(branch, changed_files, all_docs)
                span.set_attribute("fleet.chunk_count", count)

                import datetime

                with _status_lock:
                    _index_status[f"{project}:{branch}"] = {
                        "status": "indexed",
                        "chunk_count": count,
                        "file_count": len(changed_files),
                        "last_sync": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "error": None,
                    }
            except Exception as exc:
                logger.exception("Branch indexing failed for %s:%s", project, branch)
                with _status_lock:
                    _index_status[f"{project}:{branch}"] = {
                        "status": "failed",
                        "chunk_count": 0,
                        "file_count": 0,
                        "last_sync": None,
                        "error": str(exc),
                    }
            finally:
                span.end()

        thread = threading.Thread(target=_run_branch, daemon=True)
        thread.start()
        return {"project": project, "branch": branch, "status": "indexing"}

    # Check if already indexed and not forcing
    if not force and db.has_collection(collection_name):
        count = db.count(collection_name)
        span.set_attribute("fleet.chunk_count", count)
        span.end()
        with _status_lock:
            _index_status[project] = {
                "status": "indexed",
                "file_count": 0,
                "chunk_count": count,
                "last_sync": None,
                "error": None,
            }
        return {"project": project, "status": "indexed", "chunk_count": count}

    # Set status to indexing
    with _status_lock:
        _index_status[project] = {
            "status": "indexing",
            "file_count": 0,
            "chunk_count": 0,
            "last_sync": None,
            "error": None,
        }

    def _run():
        try:
            if force and db.has_collection(collection_name):
                db.drop_collection(collection_name)

            def _progress(current: int, total: int, msg: str):
                with _status_lock:
                    _index_status[project]["file_count"] = total

            chunk_count = _index(
                root=Path(path).resolve(),
                project_name=project,
                db=db,
                embedder=embedder,
                progress=_progress,
                extra_ignore_patterns=ignore_patterns,
            )
            span.set_attribute("fleet.chunk_count", chunk_count)
            import datetime

            with _status_lock:
                _index_status[project].update(
                    {
                        "status": "indexed",
                        "chunk_count": chunk_count,
                        "last_sync": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                )
        except Exception as exc:
            logger.exception("Indexing failed for %s", project)
            with _status_lock:
                _index_status[project].update({"status": "failed", "error": str(exc)})
        finally:
            span.end()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"project": project, "status": "indexing"}


# ---------------------------------------------------------------------------
# Tool: search_code
# ---------------------------------------------------------------------------


@mcp.tool(description="Semantic code search across indexed codebases.")
async def search_code(
    query: str,
    path: str | None = None,
    limit: int = 10,
    extension_filter: str | None = None,
    branch: str | None = None,
) -> list[dict[str, Any]]:
    """Search indexed code chunks by semantic similarity.

    When *branch* is provided and *path* is set, searches the branch
    overlay first (higher priority) then falls back to the base collection,
    excluding files already found in the overlay.
    """
    await _ensure_background_sync()
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.search") as span:
        span.set_attribute("fleet.query_hash", hash_content(query))
        span.set_attribute("fleet.limit", limit)

        limit = min(max(limit, 1), 100)
        config = _get_config()
        db = _get_db(config)
        embedder = _get_embedder(config)

        vector = await embedder.aembed(query)
        where = None
        if extension_filter:
            where = {"language": extension_filter}

        # Branch-aware search via BranchIndex
        if branch and path:
            from .fleet.branch_index import BranchIndex

            project = _project_name_from_path(path)
            bi = BranchIndex(db, project)
            hits = await asyncio.to_thread(
                bi.search, query_vector=vector, branch=branch, limit=limit, where=where
            )
            results: list[dict[str, Any]] = []
            for hit in hits:
                meta = hit.get("metadata", {})
                results.append(
                    {
                        "file_path": meta.get("file_path", ""),
                        "start_line": meta.get("start_line"),
                        "end_line": meta.get("end_line"),
                        "snippet": hit.get("content", ""),
                        "score": hit.get("score", 0.0),
                        "project": meta.get("project_name", ""),
                    }
                )
            span.set_attribute("fleet.result_count", len(results))
            span.set_attribute("fleet.cache_hits", embedder.cache_hits)
            span.set_attribute("fleet.cache_misses", embedder.cache_misses)
            return results

        # Determine which collections to search
        if path:
            project = _project_name_from_path(path)
            collections = [f"code_{project}"]
        else:
            collections = await asyncio.to_thread(db.list_collections)
            collections = [c for c in collections if c.startswith("code_")]

        results = []
        for col_name in collections:
            has_col = await asyncio.to_thread(db.has_collection, col_name)
            if not has_col:
                continue
            hits = await asyncio.to_thread(
                db.search, col_name, vector=vector, limit=limit, where=where
            )
            for hit in hits:
                meta = hit.get("metadata", {})
                results.append(
                    {
                        "file_path": meta.get("file_path", ""),
                        "start_line": meta.get("start_line"),
                        "end_line": meta.get("end_line"),
                        "snippet": hit.get("content", ""),
                        "score": hit.get("score", 0.0),
                        "project": meta.get("project_name", ""),
                    }
                )

        results.sort(key=lambda r: r["score"], reverse=True)
        final = results[:limit]
        span.set_attribute("fleet.result_count", len(final))
        span.set_attribute("fleet.cache_hits", embedder.cache_hits)
        span.set_attribute("fleet.cache_misses", embedder.cache_misses)
        return final


# ---------------------------------------------------------------------------
# Tool: clear_index
# ---------------------------------------------------------------------------


@mcp.tool(description="Drop a project's ChromaDB collection and reset status.")
async def clear_index(path: str) -> dict[str, str]:
    """Remove indexed data for a project."""
    project = _project_name_from_path(path)
    collection_name = f"code_{project}"
    config = _get_config()
    db = _get_db(config)

    if db.has_collection(collection_name):
        db.drop_collection(collection_name)

    # Remove merkle snapshot
    merkle_file = config.merkle_path / f"{project}.json"
    if merkle_file.exists():
        merkle_file.unlink()

    with _status_lock:
        _index_status.pop(project, None)
    return {"project": project, "status": "cleared"}


# ---------------------------------------------------------------------------
# Tool: get_branches
# ---------------------------------------------------------------------------


@mcp.tool(description="List indexed branches for a project with chunk counts.")
async def get_branches(path: str) -> list[dict[str, Any]]:
    """Return branches that have overlay collections for the given project."""
    from .fleet.branch_index import BranchIndex

    project = _project_name_from_path(path)
    config = _get_config()
    db = _get_db(config)
    bi = BranchIndex(db, project)
    return bi.list_branches()


# ---------------------------------------------------------------------------
# Tool: cleanup_branch
# ---------------------------------------------------------------------------


@mcp.tool(description="Drop a branch overlay after merge/delete. Optionally re-index base.")
async def cleanup_branch(
    path: str,
    branch: str,
    reindex_base: bool = False,
) -> dict[str, Any]:
    """Drop the overlay collection for *branch* and optionally re-index base."""
    from .fleet.branch_index import BranchIndex

    project = _project_name_from_path(path)
    config = _get_config()
    db = _get_db(config)
    bi = BranchIndex(db, project)

    dropped = bi.drop_branch(branch)

    result: dict[str, Any] = {
        "project": project,
        "branch": branch,
        "dropped": dropped,
    }

    if reindex_base:
        # Trigger a force re-index of the base collection
        reindex_result = await index_codebase(path=path, force=True)
        result["reindex_status"] = reindex_result.get("status")

    return result


# ---------------------------------------------------------------------------
# Tool: clear_embedding_cache
# ---------------------------------------------------------------------------


@mcp.tool(description="Clear the embedding vector cache. Forces re-embedding on next use.")
async def clear_embedding_cache() -> dict[str, str]:
    """Wipe all cached embedding vectors."""
    from .embedding.cache import EmbeddingCache

    cfg = _get_config()
    cache = EmbeddingCache(cfg.embed_cache_path)
    cache.clear()
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Tool: get_index_status
# ---------------------------------------------------------------------------


@mcp.tool(description="Get indexing status for a project.")
async def get_index_status(path: str) -> dict[str, Any]:
    """Return current index status for the given project path."""
    project = _project_name_from_path(path)
    collection_name = f"code_{project}"
    config = _get_config()
    db = _get_db(config)

    with _status_lock:
        status = dict(_index_status[project]) if project in _index_status else None
    if status is not None:
        # Refresh chunk count from DB if indexed
        if status["status"] == "indexed" and db.has_collection(collection_name):
            status["chunk_count"] = db.count(collection_name)
        status["project"] = project
        return status

    # Check DB directly
    if db.has_collection(collection_name):
        count = db.count(collection_name)
        return {
            "project": project,
            "status": "indexed",
            "file_count": 0,
            "chunk_count": count,
            "last_sync": None,
            "error": None,
        }

    return {
        "project": project,
        "status": "not_indexed",
        "file_count": 0,
        "chunk_count": 0,
        "last_sync": None,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Tool: find_symbol
# ---------------------------------------------------------------------------


@mcp.tool(description="Find symbol definitions in indexed code (functions, classes, etc).")
async def find_symbol(
    name: str,
    file_path: str | None = None,
    symbol_type: str | None = None,
) -> list[dict[str, Any]]:
    """Search for symbol definitions by name in AST-indexed chunks."""
    config = _get_config()
    db = _get_db(config)

    collections = [c for c in db.list_collections() if c.startswith("code_")]
    results: list[dict[str, Any]] = []

    for col_name in collections:
        col = db._client.get_collection(name=col_name)

        where: dict[str, Any] = {"name": name}
        if symbol_type:
            where = {"$and": [{"name": name}, {"chunk_type": symbol_type}]}

        try:
            hits = col.get(where=where, include=["documents", "metadatas"])
        except Exception:
            continue

        ids = hits.get("ids", [])
        docs = hits.get("documents", [])
        metas = hits.get("metadatas", [])

        for i, doc_id in enumerate(ids):
            meta = metas[i] if metas else {}
            if file_path and meta.get("file_path") != file_path:
                continue
            results.append(
                {
                    "file_path": meta.get("file_path", ""),
                    "start_line": meta.get("start_line"),
                    "end_line": meta.get("end_line"),
                    "snippet": docs[i] if docs else "",
                    "symbol_type": meta.get("chunk_type", ""),
                    "project": meta.get("project_name", ""),
                }
            )

    return results


# ---------------------------------------------------------------------------
# Tool: get_change_impact
# ---------------------------------------------------------------------------


@mcp.tool(description="Find code affected by changes to given files or symbols.")
async def get_change_impact(
    file_paths: list[str] | None = None,
    symbol_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Find chunks that reference the given files or symbols."""
    config = _get_config()
    db = _get_db(config)

    collections = [c for c in db.list_collections() if c.startswith("code_")]
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    search_terms: list[str] = []
    if file_paths:
        for fp in file_paths:
            search_terms.append(Path(fp).name)
            # Also search for import paths
            stem = Path(fp).stem
            search_terms.append(stem)
    if symbol_names:
        search_terms.extend(symbol_names)

    for col_name in collections:
        col = db._client.get_collection(name=col_name)
        for term in search_terms:
            try:
                hits = col.get(
                    where_document={"$contains": term},
                    include=["documents", "metadatas"],
                )
            except Exception:
                continue

            ids = hits.get("ids", [])
            docs = hits.get("documents", [])
            metas = hits.get("metadatas", [])

            for i, doc_id in enumerate(ids):
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                meta = metas[i] if metas else {}
                results.append(
                    {
                        "file_path": meta.get("file_path", ""),
                        "start_line": meta.get("start_line"),
                        "end_line": meta.get("end_line"),
                        "snippet": docs[i] if docs else "",
                        "matched_term": term,
                        "project": meta.get("project_name", ""),
                    }
                )

    return results


# ---------------------------------------------------------------------------
# Tool: get_dependents
# ---------------------------------------------------------------------------


@mcp.tool(description="Find what calls/imports a given symbol (incoming edges).")
async def get_dependents(
    symbol_name: str,
    file_path: str | None = None,
    depth: int = 1,
) -> list[dict[str, Any]]:
    """BFS search for chunks that reference the symbol, up to `depth` levels."""
    depth = min(max(depth, 1), 5)
    config = _get_config()
    db = _get_db(config)

    collections = [c for c in db.list_collections() if c.startswith("code_")]
    seen: set[str] = set()
    current_terms: set[str] = {symbol_name}
    all_results: list[dict[str, Any]] = []

    for level in range(depth):
        next_terms: set[str] = set()
        for col_name in collections:
            col = db._client.get_collection(name=col_name)
            for term in current_terms:
                try:
                    hits = col.get(
                        where_document={"$contains": term},
                        include=["documents", "metadatas"],
                    )
                except Exception:
                    continue

                ids = hits.get("ids", [])
                docs = hits.get("documents", [])
                metas = hits.get("metadatas", [])

                for i, doc_id in enumerate(ids):
                    if doc_id in seen:
                        continue
                    seen.add(doc_id)
                    meta = metas[i] if metas else {}
                    if file_path and meta.get("file_path") == file_path:
                        continue  # skip the definition file itself
                    entry = {
                        "file_path": meta.get("file_path", ""),
                        "start_line": meta.get("start_line"),
                        "end_line": meta.get("end_line"),
                        "snippet": docs[i] if docs else "",
                        "depth": level + 1,
                        "project": meta.get("project_name", ""),
                    }
                    all_results.append(entry)
                    # For next BFS level, use any symbol names found in these chunks
                    chunk_name = meta.get("name")
                    if chunk_name and chunk_name != symbol_name:
                        next_terms.add(chunk_name)

        current_terms = next_terms
        if not current_terms:
            break

    return all_results


# ---------------------------------------------------------------------------
# Tool: find_similar_code
# ---------------------------------------------------------------------------


@mcp.tool(description="Find code chunks similar to a given snippet.")
async def find_similar_code(
    code_snippet: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Embed a snippet and search for similar indexed chunks."""
    limit = min(max(limit, 1), 100)
    config = _get_config()
    db = _get_db(config)
    embedder = _get_embedder(config)

    vector = await embedder.aembed(code_snippet)
    collections = [c for c in db.list_collections() if c.startswith("code_")]

    results: list[dict[str, Any]] = []
    for col_name in collections:
        if not db.has_collection(col_name):
            continue
        hits = db.search(col_name, vector=vector, limit=limit)
        for hit in hits:
            meta = hit.get("metadata", {})
            results.append(
                {
                    "file_path": meta.get("file_path", ""),
                    "start_line": meta.get("start_line"),
                    "end_line": meta.get("end_line"),
                    "snippet": hit.get("content", ""),
                    "score": hit.get("score", 0.0),
                    "project": meta.get("project_name", ""),
                }
            )

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


# ---------------------------------------------------------------------------
# Memory tools
# ---------------------------------------------------------------------------


@mcp.tool(description="Search agent memory (hybrid FTS + semantic).")
async def memory_search(
    query: str,
    top_k: int = 10,
    node_type: str | None = None,
) -> list[dict[str, Any]]:
    """Search stored agent memories."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.memory.search") as span:
        span.set_attribute("fleet.query_hash", hash_content(query))
        span.set_attribute("fleet.top_k", top_k)
        top_k = min(max(top_k, 1), 100)
        mem = _get_memory()
        hits = mem.memory_search(query, top_k=top_k, node_type=node_type)
        span.set_attribute("fleet.result_count", len(hits))
        return [
            {
                "id": h.id,
                "node_type": h.node_type,
                "content": h.content,
                "summary": h.summary,
                "score": h.score,
                "file_path": h.file_path,
            }
            for h in hits
        ]


@mcp.tool(description="Store a new memory node.")
async def memory_store(
    node_type: str,
    content: str,
    summary: str | None = None,
    keywords: list[str] | None = None,
    file_path: str | None = None,
    line_range: str | None = None,
    source: str = "agent",
    project_path: str | None = None,
) -> dict[str, str]:
    """Store a memory node with optional file anchor."""
    tracer = get_tracer()
    with tracer.start_as_current_span("fleet.memory.store") as span:
        span.set_attribute("fleet.content_hash", hash_content(content))
        span.set_attribute("fleet.node_type", node_type)
        mem = _get_memory()
        node_id = mem.memory_store(
            node_type=node_type,
            content=content,
            summary=summary,
            keywords=keywords,
            file_path=file_path,
            line_range=line_range,
            source=source,
            project_path=project_path,
        )
        return {"id": node_id, "status": "stored"}


@mcp.tool(description="Promote a project memory to global scope.")
async def memory_promote(
    memory_id: str,
    target_scope: str | None = None,
) -> dict[str, str]:
    """Promote a memory node to a different (or global) scope."""
    mem = _get_memory()
    mem.memory_promote(memory_id, target_scope=target_scope)
    return {"id": memory_id, "status": "promoted"}


@mcp.tool(description="Remove ghost chunks whose source files no longer exist.")
async def reconcile(
    path: str,
) -> dict[str, Any]:
    """Run full reconciliation on a project's collection.

    Scans all chunks and deletes any whose source file no longer exists on disk.
    """
    from .sync.reconciler import ChunkReconciler

    project = _project_name_from_path(path)
    collection_name = f"code_{project}"
    config = _get_config()
    db = _get_db(config)

    if not db.has_collection(collection_name):
        return {"project": project, "status": "no_collection", "orphans_removed": 0}

    # Build set of existing files by scanning the project directory
    root = Path(path).resolve()
    if not root.is_dir():
        return {"project": project, "status": "path_not_found", "orphans_removed": 0}

    from .splitter.file_scanner import scan_files

    existing_files: set[str] = set()
    for file_path, _lang, _content in scan_files(root):
        existing_files.add(str(file_path.relative_to(root)))

    reconciler = ChunkReconciler(db)
    removed = reconciler.full_reconcile(collection_name, existing_files)

    return {"project": project, "status": "reconciled", "orphans_removed": removed}


@mcp.tool(description="Check for stale file anchors in memory.")
async def stale_check(
    project_path: str | None = None,
) -> list[dict[str, str]]:
    """Find memory anchors whose files have changed."""
    mem = _get_memory()
    stale = mem.stale_check(project_path=project_path)
    return [
        {
            "memory_id": s.memory_id,
            "anchor_id": s.anchor_id,
            "file_path": s.file_path,
            "stored_hash": s.stored_hash,
            "current_hash": s.current_hash,
        }
        for s in stale
    ]


# ---------------------------------------------------------------------------
# Tool: fleet_register
# ---------------------------------------------------------------------------


@mcp.tool(
    description="Register an agent session. Call once when starting work. "
    "Tracks which agents are active, their worktrees, and branches."
)
async def fleet_register(
    agent_id: str,
    project: str,
    worktree_path: str | None = None,
    branch: str | None = None,
) -> dict[str, str]:
    """Register or update an agent session. Idempotent."""
    from .fleet.sessions import register_agent

    cfg = _get_config()
    return register_agent(
        db_path=cfg.fleet_db_path,
        agent_id=agent_id,
        project=project,
        worktree_path=worktree_path,
        branch=branch,
    )


@mcp.tool(
    description="List all registered agent sessions with status (active, idle, disconnected)."
)
async def fleet_agents() -> list[dict[str, Any]]:
    """Return all agent sessions with current statuses."""
    from .fleet.sessions import list_agents

    cfg = _get_config()
    return list_agents(cfg.fleet_db_path)


# ---------------------------------------------------------------------------
# Tool: get_fleet_stats
# ---------------------------------------------------------------------------


@mcp.tool(
    description="Get fleet-wide statistics: chunk counts, memory nodes, "
    "locks, cache size, active agents."
)
async def fleet_stats() -> dict[str, Any]:
    """Collect and return current fleet metrics."""
    from .fleet.stats import get_fleet_stats as _get_stats

    cfg = _get_config()
    return _get_stats(
        chroma_path=cfg.chroma_path,
        memory_db_path=cfg.memory_db_path,
        fleet_db_path=cfg.fleet_db_path,
        embed_cache_path=cfg.embed_cache_path,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _make_reindex_callback(config):
    """Build a callback for BackgroundSync that re-indexes changed/removed files."""

    def _reindex(changed_files: list[str], removed_files: list[str]) -> None:
        # Delete stale chunks for changed/removed files, then re-index changed files.
        try:
            from .indexer import index_files
            from .sync.reconciler import ChunkReconciler

            db = _get_db(config)
            embedder = _get_embedder(config)
            reconciler = ChunkReconciler(db)

            # Identify which projects are affected (group by top-level dir)
            projects: dict[str, list[str]] = {}
            for fp in changed_files:
                parts = Path(fp).parts
                project = parts[0] if parts else "unknown"
                projects.setdefault(project, []).append(fp)

            for project, files in projects.items():
                collection_name = f"code_{project}"
                logger.info("Re-indexing %d changed files in %s", len(files), project)
                if db.has_collection(collection_name):
                    for fp in files:
                        reconciler.reconcile_file(collection_name, fp)

                # Re-index the changed files
                code_root = Path.home() / "CODE"
                project_root = code_root / project
                if project_root.is_dir():
                    result = index_files(
                        root=project_root,
                        project_name=project,
                        file_paths=files,
                        db=db,
                        embedder=embedder,
                    )
                    logger.info(
                        "Re-indexed %s: %d chunks inserted, %d files ok, %d failed",
                        project,
                        result.chunks_inserted,
                        result.files_succeeded,
                        result.files_failed,
                    )
                    if result.errors:
                        for fp, err in result.errors.items():
                            logger.warning("Failed to re-index %s: %s", fp, err)

            # Delete chunks for removed files (grouped by project)
            removed_by_project: dict[str, list[str]] = {}
            for fp in removed_files:
                parts = Path(fp).parts
                project = parts[0] if parts else "unknown"
                removed_by_project.setdefault(project, []).append(fp)

            for project, files in removed_by_project.items():
                collection_name = f"code_{project}"
                if db.has_collection(collection_name):
                    reconciler.reconcile_removed_files(collection_name, files)

        except Exception:
            logger.exception("Reindex callback failed")

    return _reindex


async def _start_background_sync(config):
    """Start background sync and optional file watching for indexed projects."""
    from .sync.background import BackgroundSync

    db = _get_db(config)
    collections = [c for c in db.list_collections() if c.startswith("code_")]
    syncs: list[BackgroundSync] = []

    code_root = Path.home() / "CODE"

    # If file watching is enabled, create a watcher and increase poll interval
    watcher = None
    if config.file_watching:
        from .sync.watcher import FileWatcher

        watcher = FileWatcher()
        logger.info("File watching: enabled")
    else:
        logger.info("File watching: disabled (polling every %ds)", config.sync_interval_seconds)

    for col_name in collections:
        project_name = col_name.removeprefix("code_")
        project_path = code_root / project_name
        if not project_path.is_dir():
            logger.warning("Skipping sync for %s: directory not found", project_path)
            continue

        callback = _make_reindex_callback(config)

        # Register file watcher for near-instant sync
        if watcher is not None:
            watcher.watch(project_name, project_path, callback)

        bg = BackgroundSync(
            config=config,
            project_path=project_path,
            project_name=project_name,
            reindex_callback=callback,
        )
        await bg.start()
        syncs.append(bg)
        interval = config.sync_interval_seconds
        logger.info("Background sync started for %s (every %ds)", project_name, interval)

    return syncs, watcher


_bg_syncs: list = []
_file_watcher = None
_bg_syncs_started = False

# Auto-registration state
_agent_id: str | None = None
_HEARTBEAT_INTERVAL = 30.0  # seconds


def _register_agent(config) -> None:
    """Detect context and register agent session on startup."""
    global _agent_id
    import subprocess
    import uuid

    from .fleet.sessions import register_agent

    cwd = Path.cwd().resolve()
    project = cwd.name
    worktree = str(cwd)
    branch = None
    _agent_id = f"agent-{uuid.uuid4().hex[:12]}"

    # Detect git toplevel for project name
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if toplevel.returncode == 0:
            project = Path(toplevel.stdout.strip()).name
    except Exception:
        pass

    # Detect git branch
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
    except Exception:
        pass

    register_agent(
        db_path=config.fleet_db_path,
        agent_id=_agent_id,
        project=project,
        worktree_path=worktree,
        branch=branch,
    )
    logger.info(
        "Agent registered: %s (project=%s, branch=%s)",
        _agent_id,
        project,
        branch,
    )


def _start_heartbeat_thread(config) -> None:
    """Background thread that heartbeats the agent session."""
    import time

    from .fleet.sessions import heartbeat_agent

    def _loop():
        while True:
            time.sleep(_HEARTBEAT_INTERVAL)
            if _agent_id:
                try:
                    heartbeat_agent(config.fleet_db_path, _agent_id)
                except Exception:
                    pass

    t = threading.Thread(target=_loop, daemon=True, name="agent-heartbeat")
    t.start()


async def _ensure_background_sync():
    """Start background sync lazily on first tool call."""
    global _bg_syncs, _file_watcher, _bg_syncs_started
    if _bg_syncs_started:
        return
    _bg_syncs_started = True
    from .config import Config

    config = Config()
    _bg_syncs, _file_watcher = await _start_background_sync(config)
    logger.info("Background sync active for %d projects", len(_bg_syncs))


def main():
    """Start the MCP server via stdio transport."""
    from .config import Config

    config = Config()
    logger.info("Fleet-Mem MCP server starting")
    logger.info("ChromaDB path: %s", config.chroma_path)
    logger.info("Ollama host: %s", config.ollama_host)
    logger.info("Embedding model: %s", config.ollama_embed_model)
    logger.info("Memory DB: %s", config.memory_db_path)

    # Auto-register agent session
    _register_agent(config)
    _start_heartbeat_thread(config)

    # Start stats socket if configured
    if config.stats_sock:
        from .stats_server import start_stats_server

        sock_path = start_stats_server(config, sock_path=Path(config.stats_sock))
        logger.info("Stats socket: %s", sock_path)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
