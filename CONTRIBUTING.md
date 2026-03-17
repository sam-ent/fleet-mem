# Contributing to fleet-mem

Thanks for your interest! fleet-mem is solo-maintained, so contributions are welcome but gated to keep quality high and maintenance burden low.

## Ground rules

1. **Open an issue first.** All PRs must reference an existing issue. Do not submit unsolicited PRs — they will be closed.
2. **Wait for the `accepted` label.** Only issues labeled `accepted` are ready for contribution. This prevents wasted effort on features that won't be merged.
3. **One change per PR.** Keep PRs focused on a single issue. No drive-by refactors, style changes, or "while I'm here" additions.
4. **No bot PRs.** Automated PRs from bots (other than Dependabot) will be closed without review.

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
.venv/bin/ruff format --check src/ tests/
```

## Submitting a PR

1. Fork the repo and create a branch from `main`
2. Make your changes, referencing the issue number
3. Ensure tests pass and lint is clean
4. Update CHANGELOG.md for user-facing changes
5. Open a PR against `main` using the PR template

## What gets merged

- Bug fixes with tests
- Features that have been discussed and accepted
- Documentation improvements

## What doesn't get merged

- Unsolicited refactors or style changes
- PRs without a linked issue
- Changes that break existing tests
- Large PRs that try to do multiple things
