# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] - 2026-04-30

### Added

- **Tokenizer-aware char-cap** (#45) — opt-in via the new `[tokenizer-aware]` extra
  (`pip install fleet-mem[tokenizer-aware]`). Sets `Config.max_chunk_tokens` to
  count tokens via the embed model's actual tokenizer instead of approximating
  with characters. Falls back to char-cap when `tokenizers` is unavailable or
  the model is unmapped. Eliminates the bisect-recovery overhead that the
  char-cap path incurs on dense (non-English / code-heavy) content.
- `Embedder.get_tokenizer()` protocol method (#45) — returns a `tokenizers.Tokenizer`
  if the underlying model has a known HuggingFace tokenizer mapping; `None` otherwise.
- `Config.max_chunk_tokens: int | None = None` field (#45) — when set + tokenizer
  available, takes precedence over `max_chunk_chars`.
- **`DimMismatchError` exception** (#47) in `fleet_mem.vectordb.errors` — raised
  when an embed model's vector dimension doesn't match an existing ChromaDB
  collection's stored dimension. Carries `model_name`, `model_dim`,
  `collection_name`, `collection_dim` for programmatic recovery.

### Fixed

- **Embed-model dim-mismatch detection** (#47) — `ChromaDBStore.insert` and
  `ChromaDBStore.search` now validate vector dimensions against collection
  metadata before delegating to ChromaDB. Previously, switching embed models
  between different output dimensions could either raise a late chromadb
  error during inserts OR silently return ranked-but-meaningless results
  during queries.
- **Branch-indexing cap enforcement** (#44) — `_run_branch` in `fleet_mem.server`
  now delegates to `indexer._split_file` for chunking, eliminating a duplicate
  chunker invocation that bypassed the chunk-size cap added in #34. Cap
  enforcement is now single-source-of-truth across `index_codebase`,
  `index_files`, and the branch-indexing path.
- **ChromaDB intra-batch ID collisions** (#41) — `ChromaDBStore.insert` now
  dedupes documents by ID with last-wins semantics before delegating to
  ChromaDB's upsert. Previously, `_chunk_id` collisions (e.g., from AST nested
  nodes sharing line ranges) raised `DuplicateIDError` and aborted the entire
  batch, losing all in-flight chunks for the run.
- **Splitter infinite loop** (#36) — `splitter.split_text` previously could
  converge to two distinct fixed points (`start=-300, end=0` and `start=0,
  end=2`) on inputs with short prefix + long single-line body. Both fixed
  points eliminated via a `truncated_at_newline` sentinel that skips overlap
  at natural newline boundaries.
- **Bisect depth + per-text fallback reachability** (#38) — embedder's bisect
  recovery path now uses size-based termination (recurse until `batch=1`)
  instead of a fixed depth cap. The per-text mean-vector fallback that #34
  introduced is now actually reachable when individual texts exceed model
  context.
- **`ResponseError` preservation** (#32) — `OllamaEmbedding` now preserves
  HTTP status code and server-side error message when re-raising as
  `ConnectionError`, with `from err` chain. Previously, all `ResponseError`
  instances (400, 404, 500) were masked as a single misleading
  "Cannot reach Ollama" message.
- **Chunker char-cap** (#34) — initial char-based cap on chunk emission via
  `Config.max_chunk_chars` (env: `FLEET_MEM_MAX_CHUNK_CHARS`, default 5000).
  Splits oversized chunks on newline boundaries before embedding. Includes
  bisect scaffolding for the embedder side.
- **`scripts/index-repos.sh` exit code propagation** (#33) — now returns
  non-zero when any repo failed; prints `Indexed N/M repos.` summary line;
  supports `FAIL_FAST=1` env var for early-exit on first failure. Bulk
  callers (CI, orchestration scripts) now get accurate success/fail signals.

[0.9.0]: https://github.com/sam-ent/fleet-mem/compare/v0.8.0...v0.9.0

## [0.4.0] - Unreleased

### Added

- Agent session registry: `fleet_register` and `fleet_agents` MCP tools for tracking which agents are connected, their worktrees, branches, and activity status
- Agents tab in TUI monitor: real-time view of all registered agents with color-coded status (active/idle/disconnected)
- Automatic session status management: idle after 2 min, disconnected after 5 min, pruned after 24h
- Coordination-plane tracing: 10 new OTel spans covering agent registration, lock, subscription, notification, and merge operations
- Structured logging via structlog with OpenTelemetry trace ID/span ID injection (log-to-trace correlation)
- Fleet monitor TUI (`fleet-mem monitor`): Textual-based terminal dashboard with tabs, sparklines, agent filtering
- Unix domain socket stats server (0600 perms, no network exposure) for TUI communication
- Detailed stats mode: individual lock, subscription, and notification rows for rich monitoring
- `fleet-mem` CLI entry point with `monitor` subcommand
- `monitor` optional dependency group (`pip install fleet-mem[monitor]`)
- Docker: stats socket exposed via named volume for host-side monitoring

### Changed

- License changed from MIT to AGPL-3.0 for all original code in fleet-mem
- Logging migrated from stdlib `logging` to `structlog` across all modules
- `opentelemetry-api` and `structlog` are now explicit dependencies (OTel was previously transitive only)

### Fixed

- Dockerfile referenced stale `src/` directory (renamed to `fleet_mem/` in 0.2.0)

## [0.3.0] - 2025-03-17

### Added

- Hierarchical Merkle sync: skip unchanged directories entirely, near-instant for large repos
- Asyncio: all MCP tools converted to async, concurrent Ollama/OpenAI embedding calls
- Docker Compose deployment: fleet-mem + Ollama in one `docker compose up`
- File-watching via watchdog: near-instant sync using OS-native events (inotify/FSEvents), 500ms debounce
- FILE_WATCHING config (default: true)

### Changed

- Background poll interval increased to 3600s when file-watching is active (fallback only)
- Background sync uses asyncio.create_task instead of threading.Timer

## [0.2.0] - 2025-03-17

### Added

- xxHash (xxh3_64) replaces SHA-1 for file hashing and chunk IDs (~10x faster)
- Embedding cache: SQLite-backed vector cache, skip re-embed for unchanged chunks
- Recursive AST splitting: extract methods from classes with contextual breadcrumbs (parent signature prefix)
- Ghost chunk cleanup: reconcile ChromaDB with filesystem on every sync, `reconcile` MCP tool
- OpenAI-compatible embedding adapter (covers OpenAI, DeepSeek, Gemini, Together, Fireworks, vLLM)
- Custom embedding provider documentation (Cohere, Bedrock, HuggingFace examples)
- OpenTelemetry observability: tracing spans on index/search/memory, privacy-safe content hashing
- `fleet_stats` MCP tool for metrics without external collector
- `clear_embedding_cache` MCP tool
- Auto re-index changed files in background sync (was delete-only)
- Error resilience in sync: individual file failures logged and skipped

### Changed

- Existing snapshots trigger full re-index on first run (hash format change from SHA-1 to xxHash)

## [0.1.0] - 2025-03-16

### Added

- Semantic code search with tree-sitter AST splitting and ChromaDB vector store
- Ollama embedding adapter with auto-dimension detection and batch chunking
- Agent memory with hybrid FTS5 + semantic search (reciprocal rank fusion)
- File staleness detection via Merkle tree sync
- Branch/worktree-aware collections with overlay search
- Agent file lock registry with fnmatch conflict detection
- Cross-agent memory sharing with subscriptions and notifications
- Merge impact preview and post-merge notification
- 18 MCP tools via FastMCP stdio transport
