#!/usr/bin/env bash
set -euo pipefail

echo "Starting fleet-mem + Ollama..."
docker compose up -d

echo "Waiting for Ollama to be healthy..."
until docker compose exec ollama curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
    sleep 2
done

echo "Pulling embedding model..."
docker compose exec ollama ollama pull nomic-embed-text

echo ""
echo "fleet-mem is ready."
echo ""
echo "MCP client configuration:"
echo '  "fleet-mem": {'
echo '    "command": "docker",'
echo '    "args": ["exec", "-i", "fleet-mem-fleet-mem-1", "python", "-m", "src.server"]'
echo '  }'
echo ""
echo "To index a codebase, mount it and run:"
echo "  docker compose exec fleet-mem python -c \"from src.indexer import ...\""
