#!/usr/bin/env bash
set -euo pipefail

# Install fleet-mem: deps, directories, Ollama check, MCP registration.

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
# MCP client registration:
#   - Default (Claude Code): use the `claude` CLI (`claude mcp add -s user`),
#     which writes to ~/.claude.json — the file Claude Code actually reads.
#   - Other MCP clients (Cursor, Windsurf, etc.): set MCP_SETTINGS_FILE to the
#     client's MCP config path; the script writes the entry directly there.
MCP_SETTINGS_FILE="${MCP_SETTINGS_FILE:-}"

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
# Two paths:
#   (a) Claude Code (default) — use `claude mcp add -s user`, which writes the
#       entry into ~/.claude.json. This is the only file Claude Code reads for
#       user-scope MCP servers; ~/.claude/settings.json is NOT read for MCP.
#   (b) Other MCP clients — caller sets MCP_SETTINGS_FILE to the client's MCP
#       config path, and we merge the entry into that file directly.

MCP_REGISTERED_AT=""

if [ -z "$MCP_SETTINGS_FILE" ] && command -v claude &>/dev/null; then
    echo "Registering MCP server with Claude Code (claude mcp add -s user)..."
    # Re-register cleanly on repeat runs.
    if claude mcp get fleet-mem &>/dev/null; then
        claude mcp remove -s user fleet-mem >/dev/null 2>&1 || true
    fi
    claude mcp add fleet-mem \
        -s user \
        -e "OLLAMA_HOST=${OLLAMA_HOST}" \
        -e "OLLAMA_EMBED_MODEL=${EMBED_MODEL}" \
        -e "CHROMA_PATH=${CHROMA_DIR}" \
        -e "ANONYMIZED_TELEMETRY=False" \
        -- "${VENV_DIR}/bin/python" -m fleet_mem.server
    MCP_REGISTERED_AT="~/.claude.json (user scope, via claude mcp add)"
elif [ -n "$MCP_SETTINGS_FILE" ]; then
    echo "Registering MCP server in ${MCP_SETTINGS_FILE}..."
    mkdir -p "$(dirname "$MCP_SETTINGS_FILE")"

    MCP_ENTRY=$(cat <<JSONEOF
{
  "type": "stdio",
  "command": "${VENV_DIR}/bin/python",
  "args": ["-m", "fleet_mem.server"],
  "env": {
    "OLLAMA_HOST": "${OLLAMA_HOST}",
    "OLLAMA_EMBED_MODEL": "${EMBED_MODEL}",
    "CHROMA_PATH": "${CHROMA_DIR}",
    "ANONYMIZED_TELEMETRY": "False"
  }
}
JSONEOF
)

    if [ -f "$MCP_SETTINGS_FILE" ]; then
        cp "$MCP_SETTINGS_FILE" "${MCP_SETTINGS_FILE}.bak"
        echo "Backed up existing settings to ${MCP_SETTINGS_FILE}.bak"
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

print('Updated existing settings file')
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

print('Created new settings file')
"
    fi
    MCP_REGISTERED_AT="${MCP_SETTINGS_FILE}"
else
    echo ""
    echo "ERROR: Cannot register MCP server."
    echo "  - For Claude Code: install the 'claude' CLI"
    echo "    (https://docs.claude.com/en/docs/claude-code/quickstart)"
    echo "  - For other MCP clients: set MCP_SETTINGS_FILE to the client's"
    echo "    MCP config file path before running this script."
    exit 1
fi

echo ""
echo "Installation complete."
echo "  Venv:   ${VENV_DIR}"
echo "  Chroma: ${CHROMA_DIR}"
echo "  MCP:    registered at ${MCP_REGISTERED_AT}"
echo ""
echo "Restart your MCP client to pick up the new server."
