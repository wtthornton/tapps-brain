# Project status snapshot

**Last updated:** 2026-03-22 (America/Chicago) — BUG-001 + BUG-002 queued, EPIC-017–025 planned

Human-readable snapshot of the repo. For task order, use [`.ralph/fix_plan.md`](../../.ralph/fix_plan.md) (Ralph) or epic files under [`epics/`](./epics/).

## Quality gates

| Check | Target | Notes |
|--------|--------|--------|
| Tests | ~1683 passing | Full suite `pytest tests/` |
| Coverage | ≥ 95% (96.48%) | `tapps_brain` package |
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
| EPIC-012 | OpenClaw Integration | done | 2026-03-21 |
| EPIC-013 | Hive-Aware MCP Surface | done | 2026-03-21 |
| EPIC-014 | Hardening — Validation, Parity, Resilience, Docs | done | 2026-03-22 |
| EPIC-015 | Analytics & Operational Surface | done | 2026-03-22 |
| EPIC-016 | Test Suite Hardening — CLI gaps, concurrency, cleanup | done | 2026-03-22 |
| EPIC-017 | Code Review — Storage & Data Model | planned | — |
| EPIC-018 | Code Review — Retrieval & Scoring | planned | — |
| EPIC-019 | Code Review — Memory Lifecycle | planned | — |
| EPIC-020 | Code Review — Safety & Validation | planned | — |
| EPIC-021 | Code Review — Federation, Hive & Relations | planned | — |
| EPIC-022 | Code Review — Interfaces (MCP, CLI, IO) | planned | — |
| EPIC-023 | Code Review — Config, Profiles & Observability | planned | — |
| EPIC-024 | Code Review — Unit Tests Part 1 | planned | — |
| EPIC-025 | Code Review — Integration Tests, Benchmarks & TypeScript | planned | — |

## Current focus

**All 16 feature epics complete.** v1.2.0 released with 1683 passing tests, 96.48% coverage, 41 MCP tools, 36 CLI commands.

**Active work:**
- **BUG-001** (7 tasks): Pre-review critical fixes — tier priority, type safety, HiveStore leak, exception narrowing, version consistency, timezone standardization
- **BUG-002** (4 tasks): Source trust regression — uncommitted M2 feature breaks recall for agent-sourced memories via 0.7x multiplier pushing scores below `_MIN_SCORE=0.3` cutoff. Must fix before committing the feature.
- **EPIC-017–025** (planned): Comprehensive code review cycle — storage, retrieval, lifecycle, safety, federation, interfaces, config, unit tests, integration tests

## WSL / Windows

- Ralph and full test runs are **WSL-first** (bash, Linux `.venv`). See **`CLAUDE.md`** → *Ralph on Windows (use WSL)*.
- In WSL, activate with `source .venv/bin/activate` (not `Scripts/activate`).
