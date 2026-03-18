FROM python:3.11-slim

WORKDIR /app

# Install system deps for tree-sitter
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source
COPY fleet_mem/ fleet_mem/

# Entry point
CMD ["python", "-m", "fleet_mem.server"]
