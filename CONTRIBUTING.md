# Contributing to fleet-mem

## Reporting bugs

Open a GitHub issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version, OS, Ollama version

## Suggesting features

Open a GitHub issue with the `enhancement` label. Describe the problem and your proposed solution.

## Development setup

```bash
git clone https://github.com/sam-ent/fleet-mem.git
cd fleet-mem
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Running tests

```bash
.venv/bin/pytest tests/ -v
```

## Linting

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format src/ tests/
```

## Pull requests

1. Fork the repo
2. Create a feature branch
3. Make your changes
4. Ensure tests pass and lint is clean
5. Open a PR against `main`

Keep PRs focused on a single change. Update CHANGELOG.md for user-facing changes.
