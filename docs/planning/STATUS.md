# Project status snapshot

**Last updated:** 2026-03-21 (America/Chicago)

Human-readable snapshot of the repo. For task order, use [`.ralph/fix_plan.md`](../../.ralph/fix_plan.md) (Ralph) or epic files under [`epics/`](./epics/).

## Quality gates

| Check | Target | Notes |
|--------|--------|--------|
| Tests | ~1386 passing | Full suite `pytest tests/` |
| Coverage | ≥ 95% | `tapps_brain` package |
| Lint / format | clean | `ruff check`, `ruff format --check` |
| Types | strict | `mypy --strict src/tapps_brain/` |

## Storage / schema

- **SQLite schema version:** **v7** (forward migrations from v1).
- **v5:** bi-temporal columns (`valid_at`, `invalid_at`, `superseded_by`) for EPIC-004.
- **v6:** version bump for observability alignment (no new columns).
- **v7:** `agent_scope` column for Hive propagation (EPIC-011).
- **Hive DB:** separate SQLite at `~/.tapps-brain/hive/hive.db` with WAL, FTS5, namespace-aware schema.

## Dependencies (high level)

- **Runtime (core):** `pydantic`, `structlog`, `pyyaml` — no typer/mcp in core.
- **Extras:** `[cli]` adds `typer`; `[mcp]` adds `mcp`; `[all]` includes both.
- **Optional:** `vector` (faiss, sentence_transformers), `reranker` (cohere).
- **Dev:** test stack + `mcp` so MCP unit tests run under `uv sync --extra dev`.

Install for contributors:

```bash
uv sync --extra dev    # pytest, ruff, mypy, and mcp (needed for MCP unit tests)
uv sync --extra mcp    # MCP SDK only (e.g. running the server without dev tools)
```

## Interfaces

| Interface | Module / entry | Notes |
|-----------|----------------|--------|
| Library | `from tapps_brain import MemoryStore` | Core — zero heavy deps |
| CLI | `tapps-brain` (`tapps_brain.cli:app`) | Requires `[cli]` extra |
| MCP | `tapps-brain-mcp` (`tapps_brain.mcp_server:main`) | Requires `[mcp]` extra; stdio transport |

## Epics summary

| Epic | Title | Status | Completed |
|------|-------|--------|-----------|
| EPIC-001 | Test Suite Quality — A+ | done | 2026-03-19 |
| EPIC-002 | Integration Wiring | done | 2026-03-19 |
| EPIC-003 | Auto-Recall Orchestrator | done | 2026-03-19 |
| EPIC-004 | Bi-Temporal Fact Versioning | done | 2026-03-19 |
| EPIC-005 | CLI Tool | done | 2026-03-20 |
| EPIC-006 | Knowledge Graph | done | 2026-03-20 |
| EPIC-007 | Observability | done | 2026-03-21 |
| EPIC-008 | MCP Server | done | 2026-03-21 |
| EPIC-009 | Multi-Interface Distribution | done | 2026-03-21 |
| EPIC-010 | Configurable Memory Profiles | done | 2026-03-21 |
| EPIC-011 | Hive — Multi-Agent Shared Brain | done | 2026-03-21 |
| EPIC-012 | OpenClaw Integration | planned | target 2026-06-15 |

## Current focus

**EPIC-012 (OpenClaw)** is broken into 17 Ralph-sized tasks in `fix_plan.md` and ready for execution. EPIC-011 (Hive) was completed 2026-03-21 — added `HiveStore`, `AgentRegistry`, `PropagationEngine`, `ConflictPolicy`, hive-aware recall, 5 MCP tools, 4 CLI commands, schema v7, 71 new tests.

## WSL / Windows

- Ralph and full test runs are **WSL-first** (bash, Linux `.venv`). See **`CLAUDE.md`** → *Ralph on Windows (use WSL)*.
- In WSL, activate with `source .venv/bin/activate` (not `Scripts/activate`).
