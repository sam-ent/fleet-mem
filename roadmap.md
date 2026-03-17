# Roadmap

## Completed

- ~~Recursive AST splitting for nested methods/classes~~ (Phase 1)
- ~~Embedding cache for near-instant re-indexing~~ (Phase 1)
- ~~Ghost chunk cleanup / stale vector reconciliation~~ (Phase 1)
- ~~xxHash for faster file hashing and chunk IDs~~ (Phase 1)
- ~~Auto re-index changed files in background sync~~
- ~~OpenAI-compatible embedding adapter~~ (covers OpenAI, DeepSeek, Gemini, Together, etc.)
- ~~Custom embedding provider documentation~~ (Cohere, Bedrock, HuggingFace examples)
- ~~OpenTelemetry observability~~ (tracing spans, fleet stats tool, privacy-safe hashing)
- ~~Hierarchical Merkle sync~~ (Phase 2)
- ~~Asyncio transition~~ (Phase 2)
- ~~Docker Compose deployment~~ (Phase 2)
- ~~File-watching for near-instant sync~~ (Phase 2)

## Near-term

- Go/Rust recursive AST splitting (promote to Tier 1)
- Performance benchmarks on real codebases (10k, 50k, 100k+ files)
- MCP client configuration guides for Cursor, Windsurf, and other editors

## Medium-term

- Agent workflow templates for common multi-agent patterns
- Web dashboard for fleet status (locks, active agents, memory feed)
- Token usage estimation and budget controls
- Memory pruning / TTL-based decay for stale memories

## Long-term

- Distributed fleet coordination across multiple machines
- Integration with CI/CD pipelines for automated re-indexing on merge
- Shared fleet state across teams (optional, opt-in)
