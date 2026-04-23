#!/usr/bin/env bash
set -euo pipefail

# Index all git repositories into fleet-mem.

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
CODE_ROOT="$(pwd)"
MAX_DEPTH=2

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --root)
            CODE_ROOT="$2"
            shift 2
            ;;
        --root=*)
            CODE_ROOT="${1#*=}"
            shift
            ;;
        *)
            echo "Usage: $0 [--root <directory>]"
            exit 1
            ;;
    esac
done

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found. Run scripts/setup.sh first."
    exit 1
fi

echo "Scanning for git repos under ${CODE_ROOT} (max depth ${MAX_DEPTH})..."

repos=()
while IFS= read -r gitdir; do
    repos+=("$(dirname "$gitdir")")
done < <(find "$CODE_ROOT" -maxdepth "$MAX_DEPTH" -name ".git" -type d 2>/dev/null | sort)

echo "Found ${#repos[@]} repositories."
echo ""

failed=0
total=0

for repo in "${repos[@]}"; do
    name="$(basename "$repo")"
    total=$((total+1))
    echo "Indexing: ${name} (${repo})"

    if ! "${VENV_PYTHON}" -c "
import sys
sys.path.insert(0, sys.argv[3])

from pathlib import Path
from fleet_mem.config import Config
from fleet_mem.indexer import index_codebase
from fleet_mem.vectordb.chromadb_store import ChromaDBStore
from fleet_mem.embedding.ollama_embed import OllamaEmbedding

config = Config()
db = ChromaDBStore(config.chroma_path)
embedder = OllamaEmbedding(config)

project_name = sys.argv[2]
root = Path(sys.argv[1])

collection = f'code_{project_name}'
if db.has_collection(collection):
    count = db.count(collection)
    print(f'  Already indexed ({count} chunks). Use --force to re-index.')
    sys.exit(0)

def progress(current, total, msg):
    if current % 50 == 0 or current == total:
        print(f'  {current}/{total} files processed')

chunks = index_codebase(
    root=root,
    project_name=project_name,
    db=db,
    embedder=embedder,
    progress=progress,
)
print(f'  Indexed {chunks} chunks.')
" "$repo" "$name" "$PROJECT_DIR"; then
        failed=$((failed+1))
        echo "  FAILED to index ${name}. Continuing..." >&2
        if [[ "${FAIL_FAST:-0}" == "1" ]]; then
            echo "" >&2
            echo "FAIL_FAST=1 set; aborting after first failure." >&2
            echo "Indexed $((total-failed))/$total repos." >&2
            exit 2
        fi
    fi

    echo ""
done

echo "Indexed $((total-failed))/$total repos."

if (( failed > 0 )); then
    echo "All done (with ${failed} failure(s))."
    exit 1
fi

echo "All done."
