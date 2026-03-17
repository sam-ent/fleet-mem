#!/usr/bin/env bash
set -euo pipefail

# Install fleet-mem: deps, directories, Ollama check, MCP registration.

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
# MCP client settings (default: Claude Code; override for other clients)
MCP_SETTINGS_FILE="${MCP_SETTINGS_FILE:-${HOME}/.claude/settings.json}"

DEFAULT_CHROMA_DIR="${HOME}/.local/share/fleet-mem/chroma"
DEFAULT_OLLAMA_HOST="http://localhost:11434"
DEFAULT_EMBED_MODEL="nomic-embed-text"

# --- 0. Interactive configuration ---
echo "=== fleet-mem configuration ==="
echo ""

read -p "Ollama host [${DEFAULT_OLLAMA_HOST}]: " OLLAMA_INPUT
OLLAMA_HOST="${OLLAMA_INPUT:-${DEFAULT_OLLAMA_HOST}}"

read -p "ChromaDB path [${DEFAULT_CHROMA_DIR}]: " CHROMA_INPUT
CHROMA_DIR="${CHROMA_INPUT:-${DEFAULT_CHROMA_DIR}}"

EMBED_MODEL="${DEFAULT_EMBED_MODEL}"

echo ""
echo "--- Summary ---"
echo "  Ollama host:  ${OLLAMA_HOST}"
echo "  ChromaDB dir: ${CHROMA_DIR}"
echo "  Embed model:  ${EMBED_MODEL}"
echo "  Venv:         ${VENV_DIR}"
echo ""

read -p "Proceed? [Y/n]: " CONFIRM
CONFIRM="${CONFIRM:-Y}"
if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# --- 1. Python version check ---
echo "Checking Python version..."
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major="${ver%%.*}"
        minor="${ver##*.}"
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python >= 3.11 required but not found."
    exit 1
fi
echo "Using $PYTHON ($("$PYTHON" --version))"

# --- 2. Create venv if not exists ---
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at ${VENV_DIR}..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# --- 3. Install package ---
echo "Installing fleet-mem..."
"${VENV_DIR}/bin/pip" install --quiet -e "${PROJECT_DIR}[dev]"

# --- 4. Create chroma directory ---
echo "Ensuring ChromaDB directory at ${CHROMA_DIR}..."
mkdir -p "$CHROMA_DIR"

# --- 5. Test Ollama connectivity ---
echo "Checking Ollama at ${OLLAMA_HOST}..."
if curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    echo "Ollama is reachable."

    # --- 6. Pull embedding model if not present ---
    if curl -sf "${OLLAMA_HOST}/api/tags" | grep -q "\"${EMBED_MODEL}\""; then
        echo "Model ${EMBED_MODEL} already available."
    else
        echo "Pulling ${EMBED_MODEL}..."
        curl -sf "${OLLAMA_HOST}/api/pull" -d "{\"name\": \"${EMBED_MODEL}\"}" >/dev/null
        echo "Model pulled."
    fi
else
    echo "WARNING: Ollama not reachable at ${OLLAMA_HOST}. Embeddings will fail until Ollama is running."
fi

# --- 7. Register MCP server ---
echo "Registering MCP server in ${MCP_SETTINGS_FILE}..."
mkdir -p "$(dirname "$MCP_SETTINGS_FILE")"

MCP_ENTRY=$(cat <<JSONEOF
{
  "command": "${VENV_DIR}/bin/python",
  "args": ["-m", "fleet_mem.server"],
  "cwd": "${PROJECT_DIR}",
  "env": {
    "ANONYMIZED_TELEMETRY": "False"
  }
}
JSONEOF
)

if [ -f "$MCP_SETTINGS_FILE" ]; then
    cp "$MCP_SETTINGS_FILE" "${MCP_SETTINGS_FILE}.bak"
    echo "Backed up existing settings to ${MCP_SETTINGS_FILE}.bak"
    # Read existing, merge mcpServers.fleet-mem
    echo "$MCP_ENTRY" | "${VENV_DIR}/bin/python" -c "
import json, sys, os

entry = json.load(sys.stdin)
settings_file = os.path.expanduser('${MCP_SETTINGS_FILE}')

with open(settings_file) as f:
    settings = json.load(f)

settings.setdefault('mcpServers', {})
settings['mcpServers']['fleet-mem'] = entry

with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')

print('Updated existing settings.json')
"
else
    echo "$MCP_ENTRY" | "${VENV_DIR}/bin/python" -c "
import json, sys, os

entry = json.load(sys.stdin)
settings_file = os.path.expanduser('${MCP_SETTINGS_FILE}')

settings = {'mcpServers': {'fleet-mem': entry}}

with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')

print('Created new settings.json')
"
fi

echo ""
echo "Installation complete."
echo "  Venv:   ${VENV_DIR}"
echo "  Chroma: ${CHROMA_DIR}"
echo "  MCP:    registered in ${MCP_SETTINGS_FILE}"
echo ""
echo "Restart your MCP client to pick up the new server."
