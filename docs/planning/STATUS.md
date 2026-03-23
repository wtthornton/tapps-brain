# Project status snapshot

**Last updated:** 2026-03-23 (America/Chicago) — release **v1.3.0**; feedback, diagnostics, flywheel (v9–v11 schema); EPIC-033 done

**Package version (PyPI / `pyproject.toml`):** **1.3.0**

Human-readable snapshot of the repo. For task order, use [`.ralph/fix_plan.md`](../../.ralph/fix_plan.md) (Ralph) or epic files under [`epics/`](./epics/).

## Quality gates

| Check | Target | Notes |
|--------|--------|--------|
| Tests | ~2300+ collected (`pytest tests/`) | Benchmarks excluded in CI-style runs via `-m "not benchmark"` |
| Coverage | ≥ 95% | `tapps_brain` package (`--cov-fail-under=95`) |
| Lint / format | clean | `ruff check`, `ruff format --check` |
| Types | strict | `mypy --strict src/tapps_brain/` |

## Storage / schema

- **SQLite schema version:** **v11** (forward migrations from v1). See `src/tapps_brain/persistence.py` (`_SCHEMA_VERSION`).
- **v5:** bi-temporal columns (`valid_at`, `invalid_at`, `superseded_by`) for EPIC-004.
- **v6:** version bump for observability alignment (no new columns).
- **v7:** `agent_scope` column for Hive propagation (EPIC-011).
- **v8:** `integrity_hash` on `memories` (tamper detection).
- **v9:** `feedback_events` table (EPIC-029).
- **v10:** `diagnostics_history` table (EPIC-030).
- **v11:** `positive_feedback_count` / `negative_feedback_count` on `memories`, `flywheel_meta` KV (EPIC-031).
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
| EPIC-017 | Code Review — Storage & Data Model | done | 2026-03-23 |
| EPIC-018 | Code Review — Retrieval & Scoring | done | 2026-03-23 |
| EPIC-019 | Code Review — Memory Lifecycle | done | 2026-03-23 |
| EPIC-020 | Code Review — Safety & Validation | done | 2026-03-23 |
| EPIC-021 | Code Review — Federation, Hive & Relations | done | 2026-03-23 |
| EPIC-022 | Code Review — Interfaces (MCP, CLI, IO) | done | 2026-03-23 |
| EPIC-023 | Code Review — Config, Profiles & Observability | done | 2026-03-23 |
| EPIC-024 | Code Review — Unit Tests Part 1 | done | 2026-03-23 |
| EPIC-025 | Code Review — Integration Tests, Benchmarks & TypeScript | done | 2026-03-23 |
| EPIC-026 | OpenClaw Memory Replacement | done | 2026-03-23 |
| EPIC-027 | OpenClaw Full Feature Surface — All 41 MCP Tools | done | 2026-03-23 |
| EPIC-028 | OpenClaw Plugin Hardening | done | 2026-03-23 |
| EPIC-029 | Feedback Collection | done | 2026-03-23 |
| EPIC-030 | Diagnostics & Self-Monitoring | done | 2026-03-23 |
| EPIC-031 | Continuous Improvement Flywheel | done | 2026-03-23 |
| EPIC-032 | OTel GenAI semantic conventions | planned | — |
| EPIC-033 | OpenClaw Plugin SDK Alignment | done | 2026-03-23 |

## Current focus

**Shipped:** feedback (`feedback.py`, MCP/CLI), diagnostics (`diagnostics.py`, circuit breaker, `RecallResult.quality_warning`, MCP/CLI), flywheel (`evaluation.py`, `flywheel.py`, `store.process_feedback()` / `generate_report()`, MCP/CLI), schema **v11**. MCP server exposes **54** tools and **7** resources (`memory://stats`, `health`, `entries/{key}`, `metrics`, `feedback`, `diagnostics`, `report`).

**Next (see fix_plan):**
- **HOUSEKEEPING-002** — Update stale planning docs.
- **QUALITY-001** — Full QA gate (tests, lint, types).
- **EPIC-032** — OTel GenAI semantic conventions (optional telemetry export, deferred).

## WSL / Windows

- Ralph and full test runs are **WSL-first** (bash, Linux `.venv`). See **`CLAUDE.md`** → *Ralph on Windows (use WSL)*.
- In WSL, activate with `source .venv/bin/activate` (not `Scripts/activate`).
