# Contributing to tapps-brain

Thanks for helping improve tapps-brain. This project uses **uv** for environments, **pytest** with a **≥95% coverage** gate, **ruff**, and **strict mypy**.

## Setup

```bash
git clone https://github.com/wtthornton/tapps-brain.git
cd tapps-brain
uv sync --group dev
# Optional extras: cli, mcp, reranker, otel, visual (see pyproject.toml)
```

Activate the virtualenv uv creates (`.venv` at the repo root) or prefix commands with `uv run`.

## Tests

CI-style run (excludes benchmarks, enforces coverage):

```bash
pytest tests/ -v --tb=short -m "not benchmark" \
  --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95
```

Single file or test:

```bash
pytest tests/unit/test_memory_store.py -v
pytest tests/unit/test_memory_store.py::test_function_name -v
```

## Lint and types

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy --strict src/tapps_brain/
```

Auto-fix where safe:

```bash
ruff check --fix src/ tests/
ruff format src/ tests/
```

## Commits and PRs

- Prefer focused commits; reference planning stories when applicable (`feat(story-001.3): …`).
- Before a release or large change, the full gate is: `bash scripts/release-ready.sh` (see [`scripts/publish-checklist.md`](scripts/publish-checklist.md)).
- Open PRs against `main` with a short description of behavior change and risk.

### PR checklist for HTTP changes

If your PR touches `src/tapps_brain/http_adapter.py` (or any future `http/` subtree):

1. **Library parity** — the same capability must exist in `AgentBrain` (Python API).
2. **MCP parity** — the same capability must be available as an MCP tool, OR document
   why MCP is not applicable (e.g. it is an infrastructure-only probe).
3. **ADR or inline table** — reference [ADR-008](docs/planning/adr/ADR-008-no-http-without-mcp-library-parity.md)
   or add an inline justification for each new route in the PR description.
4. **OpenAPI snapshot** — update `tests/unit/test_http_adapter.py` snapshot if the
   OpenAPI spec changes (`GET /openapi.json`).

A CODEOWNERS entry on the HTTP adapter path means `@wtthornton` is auto-requested for
review on any PR that touches it. See [`.github/CODEOWNERS`](.github/CODEOWNERS).

## Reporting issues

Include repro steps, expected vs actual behavior, and versions (OS, Python, tapps-brain). Use GitHub issues with templates when available.
