# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-03-16

### Added

- Semantic code search with tree-sitter Abstract Syntax Tree (AST) splitting and ChromaDB vector store
- Ollama embedding adapter with auto-dimension detection and batch chunking
- Agent memory with hybrid FTS5 + semantic search (reciprocal rank fusion)
- File staleness detection via SHA-1 Merkle tree sync
- Branch/worktree-aware collections with overlay search
- Agent file lock registry with fnmatch conflict detection
- Cross-agent memory sharing with subscriptions and notifications
- Merge impact preview and post-merge notification
- 18 MCP tools via FastMCP stdio transport
- 155 tests across 14 test files
