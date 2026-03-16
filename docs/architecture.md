# Fleet-Mem — Architecture

## System Overview

```
 MCP Client (any)
        |
        | stdio (MCP protocol)
        v
 +------------------+
 |  MCP Server      |  src/server.py — FastMCP entry point
 |  (12 tools)      |  Lazy-loads components on first tool call
 +--------+---------+
          |
    +-----+-----+-----+-----+
    |           |             |
    v           v             v
 Code Search   Memory     Index Mgmt
    |           |             |
    v           v             v
 ChromaDB    SQLite+       Background
 (vectors)   ChromaDB      Indexer
    |        (hybrid)         |
    v           |             v
 Ollama        v           AST Splitter
 (embed)    Ollama          + Merkle
            (embed)          Sync
```

All components run locally. No cloud services, no telemetry.

## Components

### ChromaDB Store (`src/vectordb/chromadb_store.py`)

Persistent vector database for both code chunks and memory nodes.

- **Storage:** `~/.local/share/fleet-mem/chroma/` (configurable via `CHROMA_PATH`)
- **Collections:** `code_{project}` for code, `memory` for agent memory
- **Distance:** L2 (HNSW index)
- **API:** `insert`, `search`, `delete`, `count`, `drop_collection`

Wraps `chromadb.PersistentClient`. All embeddings are pre-computed externally
(Ollama) and passed as vectors — ChromaDB does not embed.

### Ollama Embeddings (`src/embedding/ollama_embed.py`)

Local embedding via Ollama HTTP API.

- **Model:** `nomic-embed-text` (768 dimensions, configurable via `OLLAMA_EMBED_MODEL`)
- **Host:** `http://localhost:11434` (configurable via `OLLAMA_HOST`)
- **Batch size:** 64 texts per request
- **Auto-detection:** Dimension probed on first call

### AST Splitter (`src/splitter/ast_splitter.py`)

Tree-sitter based code chunking. Parses source files into semantic chunks
(functions, classes, methods) preserving structural boundaries.

- **Languages:** Python, JavaScript, TypeScript (built-in); Go, Rust, Java (optional)
- **Fallback:** `text_splitter.py` for unsupported languages (line-based sliding window)
- **Metadata:** Each chunk carries `file_path`, `start_line`, `end_line`, `name`, `chunk_type`, `language`

### File Scanner (`src/splitter/file_scanner.py`)

Walks project directories respecting `.gitignore` patterns and configurable
ignore lists. Feeds files to the AST splitter.

### Merkle Sync (`src/sync/merkle.py`, `src/sync/synchronizer.py`)

Incremental re-indexing using content-addressed Merkle trees.

- **Merkle tree:** SHA-1 hash of each file, rolled up per directory
- **Snapshots:** Stored as JSON in `{chroma_path}/merkle/{project}.json`
- **Sync logic:** Compare current tree vs snapshot, re-index only changed files
- **Background:** `src/sync/background.py` runs sync on a configurable interval

### Memory Engine (`src/memory/engine.py`)

SQLite-backed storage for agent memory nodes.

- **Database:** `~/.local/share/fleet-mem/memory.db` (configurable via `MEMORY_DB_PATH`)
- **Tables:**
  - `memory_nodes` — id, node_type, content, summary, keywords, file_path, line_range, source, project_path, archived, timestamps
  - `file_anchors` — links memory nodes to file locations with hash for staleness detection
  - `memory_fts` — FTS5 virtual table for keyword search over content and summary
- **Features:** Insert, get, FTS search, file anchor management, project scoping

### Memory Embedder (`src/memory/embedder.py`)

Hybrid search combining FTS5 keyword search with ChromaDB semantic search.

- **Store:** Inserts into both SQLite (structured) and ChromaDB (vector)
- **Search:** Reciprocal Rank Fusion (RRF) of FTS5 + semantic results
- **File anchors:** Tracks file hashes to detect when anchored files change
- **Collection:** `memory` in ChromaDB

### Indexer (`src/indexer.py`)

Orchestrates the full indexing pipeline: scan files, split into chunks,
embed, and insert into ChromaDB.

## Data Flow

### Indexing Pipeline

```
1. index_codebase(path) called via MCP
2. FileScanner walks directory, respects .gitignore
3. ASTSplitter parses each file into semantic chunks
4. OllamaEmbedding embeds each chunk (batched, 64 at a time)
5. ChromaDBStore.insert() stores vectors + metadata
6. MerkleSync saves snapshot for incremental updates
```

### Code Search Flow

```
1. search_code(query) called via MCP
2. OllamaEmbedding embeds the query string
3. ChromaDBStore.search() finds nearest vectors
4. Results returned with file_path, line range, snippet, score
```

### Memory Store/Recall Flow

```
Store:
1. memory_store() called via MCP
2. MemoryEngine.insert_node() writes to SQLite + FTS5
3. OllamaEmbedding embeds content
4. ChromaDBStore.insert() stores in "memory" collection
5. Optional: file anchor created if file_path provided

Recall:
1. memory_search(query) called via MCP
2. MemoryEngine.search_fts() runs FTS5 keyword search
3. ChromaDBStore.search() runs semantic search
4. Reciprocal Rank Fusion merges both result sets
5. Top-k results returned with scores
```

## MCP Tools Reference

### Code Indexing (4 tools)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `index_codebase` | `path`, `force?`, `extensions?`, `ignore_patterns?` | Index a codebase directory (background thread) |
| `search_code` | `query`, `path?`, `limit?`, `extension_filter?` | Semantic search across indexed code |
| `clear_index` | `path` | Drop a project's index and merkle snapshot |
| `get_index_status` | `path` | Check indexing progress/status |

### Code Navigation (3 tools)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `find_symbol` | `name`, `file_path?`, `symbol_type?` | Find symbol definitions (functions, classes) |
| `get_change_impact` | `file_paths?`, `symbol_names?` | Find code affected by changes |
| `get_dependents` | `symbol_name`, `file_path?`, `depth?` | BFS for callers/importers of a symbol |

### Code Similarity (1 tool)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `find_similar_code` | `code_snippet`, `limit?` | Find chunks similar to a given snippet |

### Memory (4 tools)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `memory_store` | `node_type`, `content`, `summary?`, `keywords?`, `file_path?`, `line_range?`, `source?`, `project_path?` | Store a memory node |
| `memory_search` | `query`, `top_k?`, `node_type?` | Hybrid FTS + semantic memory search |
| `memory_promote` | `memory_id`, `target_scope?` | Promote project memory to global scope |
| `stale_check` | `project_path?` | Find memory anchors with changed files |

## Deployment

- **Runtime:** Local only. Runs as a stdio MCP server spawned by the MCP client.
- **No cloud:** All data stays on disk (ChromaDB, SQLite). No network calls except Ollama (localhost).
- **No telemetry:** ChromaDB telemetry explicitly disabled via `ANONYMIZED_TELEMETRY=False`.
- **Process model:** Single process, background threads for indexing and sync.

## Directory Structure

```
fleet-mem/
├── src/
│   ├── server.py              # MCP server + tool definitions
│   ├── config.py              # Config from env vars
│   ├── indexer.py             # Indexing pipeline orchestrator
│   ├── embedding/
│   │   ├── base.py            # Abstract Embedding class
│   │   └── ollama_embed.py    # Ollama adapter
│   ├── vectordb/
│   │   ├── base.py            # Abstract VectorDatabase class
│   │   ├── types.py           # VectorDocument dataclass
│   │   └── chromadb_store.py  # ChromaDB implementation
│   ├── splitter/
│   │   ├── ast_splitter.py    # Tree-sitter code chunker
│   │   ├── text_splitter.py   # Fallback line-based splitter
│   │   └── file_scanner.py    # Directory walker
│   ├── sync/
│   │   ├── merkle.py          # Merkle tree hashing
│   │   ├── synchronizer.py    # Incremental sync logic
│   │   └── background.py      # Background sync thread
│   └── memory/
│       ├── engine.py          # SQLite memory storage
│       └── embedder.py        # Hybrid search + embedding
├── scripts/
│   ├── import-flat-files.py   # Import markdown memory files
│   └── embed-existing-nodes.py # Backfill ChromaDB from SQLite
├── tests/
├── docs/
│   └── architecture.md        # This file
└── pyproject.toml
```

## Configuration

All configuration via environment variables with sensible defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_PATH` | `~/.local/share/fleet-mem/chroma` | ChromaDB persistent storage |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model name |
| `MEMORY_DB_PATH` | `~/.local/share/fleet-mem/memory.db` | SQLite memory database |
| `SYNC_INTERVAL` | `300` | Background sync interval (seconds) |

## Acknowledgments

The architecture of this project was inspired by [claude-context](https://github.com/zilliztech/claude-context)
by Zilliz (MIT License). While all code is an original Python implementation, the following
design patterns were informed by their TypeScript reference:

- **VectorDatabase abstraction** — interface shape for collection-based vector storage
- **Ollama embedding adapter** — auto-dimension detection on first call, batch chunking pattern
- **MerkleDAG** — SHA-1 file tree with snapshot comparison (added/removed/modified sets)
- **FileSynchronizer** — JSON snapshot persistence with background polling
- **AST splitter node-type tables** — per-language tree-sitter node types for semantic code chunking
