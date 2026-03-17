# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
