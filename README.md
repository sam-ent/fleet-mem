# fleet-mem

Shared code intelligence for agent fleets --- AST-aware semantic search + multi-agent memory with git-concurrent coordination.

## Features

- **Semantic code search** --- index codebases with tree-sitter AST splitting and search via vector similarity (ChromaDB + Ollama embeddings)
- **Agent memory** --- hybrid FTS + semantic memory store with SQLite and ChromaDB, supporting store/search/promote/stale-check workflows
- **Branch-aware indexing** --- overlay collections for feature branches so agents see their own changes without polluting the base index
- **Lock registry** --- git-concurrent coordination for multi-agent workloads
- **Cross-agent memory sharing** --- promote project-scoped memories to global scope for fleet-wide knowledge

## Quickstart

```bash
# Install dependencies, configure Ollama, register MCP server
./scripts/install.sh

# Index all git repos under current directory
./scripts/index-all.sh
```

## Configuration

All settings via environment variables. See `.env.example` for defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_PATH` | `~/.local/share/fleet-mem/chroma` | ChromaDB persistent storage |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model name |
| `MEMORY_DB_PATH` | `~/.local/share/fleet-mem/memory/agent_memory.db` | SQLite memory database |
| `SYNC_INTERVAL` | `300` | Background sync interval (seconds) |

## Acknowledgments

Architecture inspired by [claude-context](https://github.com/zilliztech/claude-context) by Zilliz (MIT License). All code is an original Python implementation.

## License

MIT
