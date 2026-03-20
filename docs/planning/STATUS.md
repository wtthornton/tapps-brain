# Project status snapshot

**Last updated:** 2026-03-20 (America/Chicago)

Human-readable snapshot of the repo. For task order, use [`.ralph/fix_plan.md`](../../.ralph/fix_plan.md) (Ralph) or epic files under [`epics/`](./epics/).

## Quality gates

| Check | Target | Notes |
|--------|--------|--------|
| Tests | ~1039 passing | Full suite `pytest tests/` |
| Coverage | ≥ 95% | `tapps_brain` package |
| Lint / format | clean | `ruff check`, `ruff format --check` |
| Types | strict | `mypy --strict src/tapps_brain/` |

## Storage / schema

- **SQLite schema version:** **v6** (forward migrations from v1).
- **v5:** bi-temporal columns (`valid_at`, `invalid_at`, `superseded_by`) for EPIC-004.
- **v6:** version bump for observability alignment (no new columns); new DBs open at v6.

## Dependencies (high level)

- **Runtime:** `pydantic`, `structlog`, `pyyaml`, **`typer`** (CLI).
- **Optional:** `mcp` (MCP server / FastMCP), `vector`, `reranker`.
- **Dev:** test stack + **`mcp`** so MCP unit tests run under `uv sync --extra dev`.

Install for contributors:

```bash
uv sync --extra dev    # pytest, ruff, mypy, and mcp (needed for MCP unit tests)
uv sync --extra mcp    # MCP SDK only (e.g. running the server without dev tools)
```

## Interfaces

| Interface | Module / entry | Notes |
|-----------|----------------|--------|
| Library | `from tapps_brain import MemoryStore` | Core |
| CLI | `python -m tapps_brain.cli` (Typer `app`) | Maintenance, store, recall, etc. |
| MCP | `python -m tapps_brain.mcp_server` | Requires `mcp` installed; stdio server |

Packaging `project.scripts` for `tapps-brain` / `tapps-brain-mcp` may be added under EPIC-009.

## Epics vs code (short)

| Epic | Doc status | Code notes |
|------|------------|------------|
| EPIC-007 Observability | planned | `MetricsCollector`, `StoreHealthReport`, `store.health()`, `store.get_metrics()` present; full instrumentation / audit query API still open per epic. |
| EPIC-008 MCP | planned | `mcp_server.py` + `tests/unit/test_mcp_server.py`; prompts / packaging polish per epic. |
| EPIC-009 Distribution | planned | Library + CLI + MCP usage documented; PyPI entry points / extras split TBD. |

## WSL / Windows

- Ralph and full test runs are **WSL-first** (bash, Linux `.venv`). See **`CLAUDE.md`** → *Ralph on Windows (use WSL)*.
- In WSL, activate with `source .venv/bin/activate` (not `Scripts/activate`).
