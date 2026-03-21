# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
