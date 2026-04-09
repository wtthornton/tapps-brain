# Project status snapshot

**Last updated:** 2026-04-09 (America/Chicago) — **v3.2.0** — EPIC-048 complete (all 6 stories done); default embedding → `BAAI/bge-small-en-v1.5`; FlashRank local reranker; porter unicode61 FTS5 tokenizer; schema reset to v1; Docker base → python:3.13-slim; **next-session handoff:** [`next-session-prompt.md`](next-session-prompt.md)

**Package version (PyPI / `pyproject.toml`):** **3.2.0**

Human-readable snapshot of the repo. For task order, use [`.ralph/fix_plan.md`](../../.ralph/fix_plan.md) (Ralph) or epic files under [`epics/`](./epics/).

## Feature intake standard

- All new `feat` proposals must follow [`FEATURE_FEASIBILITY_CRITERIA.md`](./FEATURE_FEASIBILITY_CRITERIA.md).
- Agent enforcement rules: [`AGENT_FEATURE_GOVERNANCE.md`](./AGENT_FEATURE_GOVERNANCE.md).
- Use the required scorecard + hard gates before opening or planning a feature issue.
- Proposals that skip this process are treated as incomplete and should be re-scoped, deferred, or rejected.
- Triage label filters and optional Projects setup: [`ISSUE_TRIAGE_VIEWS.md`](./ISSUE_TRIAGE_VIEWS.md).

## Quality gates

| Check | Target | Notes |
|--------|--------|--------|
| Tests | ~2300+ collected (`pytest tests/`) | Benchmarks excluded in CI-style runs via `-m "not benchmark"` |
| Coverage | ≥ 95% | `tapps_brain` package (`--cov-fail-under=95`) |
| Lint / format | clean | `ruff check`, `ruff format --check` |
| Types | strict | `mypy --strict src/tapps_brain/` |
| Release gate | green before publish | `bash scripts/release-ready.sh` (WSL/Git Bash on Windows); CI job `release-ready` |
| OpenClaw docs | no install/count drift | `python scripts/check_openclaw_docs_consistency.py` |

## Storage / schema

- **SQLite schema version:** **v17** (forward migrations from v1). See `src/tapps_brain/persistence.py` (`_SCHEMA_VERSION`).
- **v5:** bi-temporal columns (`valid_at`, `invalid_at`, `superseded_by`) for EPIC-004.
- **v6:** version bump for observability alignment (no new columns).
- **v7:** `agent_scope` column for Hive propagation (EPIC-011).
- **v8:** `integrity_hash` on `memories` (tamper detection).
- **v9:** `feedback_events` table (EPIC-029).
- **v10:** `diagnostics_history` table (EPIC-030).
- **v11:** `positive_feedback_count` / `negative_feedback_count` on `memories`, `flywheel_meta` KV (EPIC-031).
- **v12–v15:** provenance, temporal window, FSRS stability/difficulty, Bayesian access counters (see migrations in `persistence.py`).
- **v16:** `memory_group` on `memories` (optional project-local partition; GitHub **#49** v1 **closed** 2026-03-29). Relay import accepts optional per-item `memory_group` / `group` (`memory-relay.md`).
- **v17:** `embedding_model_id` on `memories` / `archived_memories` (STORY-042.2 — dense model provenance for reindex).
- **Federation hub:** `~/.tapps-brain/memory/federated.db` — `federated_memories` carries optional publisher **`memory_group`** (GitHub **#51** / EPIC-041); see `docs/guides/federation.md`.
- **Hive DB:** separate SQLite at `~/.tapps-brain/hive/hive.db` with WAL, FTS5, namespace-aware schema.

## Dependencies (high level)

- **Runtime (core):** `pydantic`, `structlog`, `pyyaml` — no typer/mcp in core.
- **Extras:** `[cli]` adds `typer`; `[mcp]` adds `mcp`; `[all]` includes both.
- **Optional:** `reranker` (cohere); `anthropic_sdk` and `openai_sdk` for LLM-as-judge evaluation.
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
| EPIC-027 | OpenClaw Full Feature Surface — MCP tools (64 as of 2026-03-29) | done | 2026-03-23 |
| EPIC-028 | OpenClaw Plugin Hardening | done | 2026-03-23 |
| EPIC-029 | Feedback Collection | done | 2026-03-23 |
| EPIC-030 | Diagnostics & Self-Monitoring | done | 2026-03-23 |
| EPIC-031 | Continuous Improvement Flywheel | done | 2026-03-23 |
| EPIC-032 | OTel GenAI semantic conventions | planned | — |
| EPIC-033 | OpenClaw Plugin SDK Alignment | done | 2026-03-23 |
| EPIC-034 | Production readiness QA remediation | done | 2026-03-24 |
| EPIC-035 | OpenClaw install and upgrade UX consistency | done | 2026-03-24 |
| EPIC-036 | Release gate hardening for OpenClaw distribution | done | 2026-03-24 |
| EPIC-037 | OpenClaw plugin SDK realignment — fix API contract | done | 2026-03-23 |
| EPIC-038 | OpenClaw plugin simplification — remove dead compat layers | done | 2026-03-23 |
| EPIC-039 | Replace custom MCP client with official @modelcontextprotocol/sdk | done | 2026-03-24 |
| EPIC-040 | tapps-brain v2.0 — research-driven upgrades | done | 2026-04-09 — all v2.0 phases shipped |
| EPIC-041 | Federation hub `memory_group`, Hive `group:<name>`, health/guides | done | 2026-04-02 — **#52** checklist closed on GitHub; **#51**/**#63**/**#64** closed |
| EPIC-042 | Retrieval stack — lexical, dense, rerank, fusion improvements | done | 2026-04-09 — all 8 stories shipped; eval/hygiene backlog-gated per PLANNING.md trigger (b) |
| EPIC-043 | Operator docs, observability, verify-integrity CLI | done | 2026-04-03 |
| EPIC-044 | Ingestion, deduplication, and lifecycle improvements | done | 2026-04-09 — all 7 stories shipped; NLI/async slice gated per trigger (c) |
| EPIC-045 | Operator docs and observability | done | 2026-04-03 |
| EPIC-046 | Operator docs | done | 2026-04-03 |
| EPIC-047 | Operator docs | done | 2026-04-03 |
| EPIC-048 | Optional / auxiliary capabilities — research and upgrades | done | 2026-04-09 — all 6 stories done (048.1–048.6) |
| EPIC-049 | multi-scope memory epic v1 | done | 2026-03-29 |
| EPIC-050 | Concurrency and runtime model | done | 2026-04-09 — all 3 stories done; lock-scope + async wrapper deferred per ADR |
| EPIC-051 | Cross-cutting §10 checklist, ADRs 001–006 | done | 2026-04-03 |
| EPIC-052 | Full Codebase Code Review — 2026-Q2 Sweep | done | 2026-04-05 — all 18 stories closed; 6 fixes landed in v2.0.4 ([`EPIC-052.md`](epics/EPIC-052.md)) |
| EPIC-053 | Per-Agent Brain Identity — isolated storage + auto-registration | done | 2026-04-09 — v3.1.0 |
| EPIC-054 | Hive Backend Abstraction Layer — pluggable storage | done | 2026-04-09 — v3.1.0 |
| EPIC-055 | PostgreSQL Hive & Federation Backend | done | 2026-04-09 — v3.1.0 |
| EPIC-056 | Declarative Group Membership & Expert Publishing | done | 2026-04-09 — v3.1.0 |
| EPIC-057 | Unified Agent API — AgentBrain facade | done | 2026-04-09 — v3.1.0 |
| EPIC-058 | Docker & Deployment Support — Postgres Hive infrastructure | done | 2026-04-09 — v3.1.0 |

## Current focus

**Shipped in v3.2.0 (2026-04-09):**
- **EPIC-048** — Optional/auxiliary capabilities: session GC retention policy + token budget (048.1); relations batch + cycle detection + max-edges cap (048.2); markdown round-trip with YAML front matter schema version (048.3); eval CI golden set + `run_eval_golden.py` artifact (048.4); doc validation strict mode + pluggable lookup engine guide (048.5); visual snapshot PNG capture — `capture_png()`, `tapps-brain visual capture`, `[visual]` extra (048.6).
- **EPIC-048 (retrieval)** — Default embedding switched to `BAAI/bge-small-en-v1.5`; FlashRank local reranker replaces Cohere; `[reranker]` extra now installs flashrank; `TAPPS_SEMANTIC_SEARCH` env var removed (always enabled).
- **Docker** — `docker/Dockerfile.migrate` base upgraded to `python:3.13-slim`.

**Shipped in v3.1.0 (2026-04-09):**
- **EPIC-053** — Per-agent brain identity: `MemoryStore(agent_id=)` routes to `{project_dir}/.tapps-brain/agents/{id}/memory.db`; auto-registration; `source_agent` auto-fill; CLI/MCP `--agent-id` passthrough; `maintenance split-by-agent` migration tool.
- **EPIC-054** — Hive backend abstraction: `HiveBackend` / `FederationBackend` / `AgentRegistryBackend` protocols in `_protocols.py`; `SqliteHiveBackend` / `SqliteFederationBackend` adapters in `backends.py`; `create_hive_backend()` / `create_federation_backend()` factories; `TAPPS_BRAIN_HIVE_DSN` / `TAPPS_BRAIN_FEDERATION_DSN` env vars; `PropagationEngine` uses `HiveBackend` protocol.
- **EPIC-055** — PostgreSQL Hive & Federation: `PostgresHiveBackend` / `PostgresConnectionManager` in `postgres_hive.py`; `pgvector` semantic search + `tsvector` FTS + `LISTEN/NOTIFY`; SQL migrations in `src/tapps_brain/migrations/hive/` and `migrations/federation/`; `PostgresFederationBackend`; conformance test suite; CLI `maintenance migrate-hive` / `hive-schema-status`.
- **EPIC-056** — Declarative groups + expert publishing: `MemoryStore(groups=[…], expert_domains=[…])` auto-creates/joins groups; expert auto-publish on `architectural`/`pattern` tiers; `save(agent_scope="group")` routing; cross-project group resolution; profile YAML `hive.groups` / `hive.expert_domains` / `hive.recall_weights`.
- **EPIC-057** — Unified `AgentBrain` API: `src/tapps_brain/agent_brain.py`; `remember()`, `recall()`, `forget()`, `learn_from_success()`, `learn_from_failure()`, `set_task_context()`; context manager; simplified `brain_*` MCP tools; top-level CLI aliases; `docs/guides/llm-brain-guide.md` + `docs/guides/agent-integration.md`.
- **EPIC-058** — Docker deployment: `docker/docker-compose.hive.yaml` (pgvector/pgvector:pg17), `docker/init-hive.sql`, `docker/Dockerfile.migrate`, `docker/README.md`; `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` auto-migration; Hive-aware health checks (`hive_connected`, `hive_latency_ms`, pool stats); `maintenance backup-hive` / `restore-hive`; `docs/guides/hive-deployment.md` + `docs/guides/hive-operations.md`.

**Previously shipped (still on `main`):** All EPIC-040–052 stories — see epic files and git log.

**Next-session prompt (copy-paste for agents):** [`next-session-prompt.md`](next-session-prompt.md).

**Next (canonical queue: [`open-issues-roadmap.md`](open-issues-roadmap.md)):**
1. **EPIC-032** — OTel GenAI semantic conventions (low priority; defer until stakeholder ask).
2. **Backlog gating:** Save-path metrics beyond ADR-006, EPIC-042 eval hygiene, NLI/async conflict wiring — triggers in [`PLANNING.md` § Optional backlog gating](PLANNING.md#optional-backlog-gating) still apply.

## READY-036 release gate (2026-03-24)

- **Script:** `scripts/release-ready.sh` — fail-fast packaging, version tests, pytest (optional skip via `SKIP_FULL_PYTEST=1`), ruff, mypy, `openclaw-plugin` npm ci/build/test.
- **Docs checker:** `scripts/check_openclaw_docs_consistency.py` — canonical `openclaw plugin install`, SKILL tool/resource counts vs baseline, runbook presence.
- **CI:** `.github/workflows/ci.yml` — `lint` runs docs checker; `release-ready` job runs the shell gate with `SKIP_FULL_PYTEST=1` after the test matrix.
- **Remediation on failure:** `scripts/publish-checklist.md`, `docs/guides/openclaw-runbook.md`, `docs/planning/epics/EPIC-036.md`.
- **Documented in:** root `README.md`, `CLAUDE.md`, `.cursor/rules/project.mdc`, `.ralph/AGENT.md`, `docs/guides/mcp.md`, `docs/guides/getting-started.md`, `docs/planning/PLANNING.md`, `CHANGELOG.md` (v2.0.3+).

## READY-035 docs consistency evidence (2026-03-24)

- Canonical runbook added: `docs/guides/openclaw-runbook.md` (PyPI + Git-only paths).
- OpenClaw command usage normalized to `openclaw plugin install` across:
  - `docs/guides/openclaw.md`
  - `docs/guides/openclaw-install-from-git.md`
  - `openclaw-plugin/README.md`
  - `openclaw-plugin/UPGRADING.md`
  - `openclaw-skill/SKILL.md` (cross-link to canonical runbook)
- Capability/status claims reconciled:
  - stale `41 MCP tools` references removed from OpenClaw guide
  - resource URIs: canonical list in `docs/generated/mcp-tools-manifest.json` (**8** resources, including `memory://agent-contract`; older copy said 7 before that URI shipped)
  - stale planned wording removed for shipped OpenClaw migration/tooling references

## READY-034 QA evidence (2026-03-24)

- Re-verified after planning-doc sync: full pytest + ruff + mypy green on Windows (Python 3.13); same counts as below.
- `ruff check src/ tests/` -> pass.
- `ruff format --check src/ tests/` -> pass.
- `mypy --strict src/tapps_brain/` -> pass.
- `cd openclaw-plugin && npm test` -> pass; timeout-path unhandled rejection eliminated.
- Full release-candidate runbook executed in one command:
  - `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`
  - `ruff check src/ tests/`
  - `ruff format --check src/ tests/`
  - `mypy --strict src/tapps_brain/`
  - `cd openclaw-plugin && npm test`
- Outcome: pass (`2341 passed, 3 skipped, 7 deselected`, coverage `95.16%`).

## WSL / Windows

- Ralph and full test runs are **WSL-first** (bash, Linux `.venv`). See **`CLAUDE.md`** → *Ralph on Windows (use WSL)*.
- In WSL, activate with `source .venv/bin/activate` (not `Scripts/activate`).
- **One checkout, one OS for `.venv`:** alternating `uv sync` on the same tree between WSL (Linux layout) and native Windows can leave `.venv` in a state where `uv` fails to replace `lib64` (access denied). Remove `.venv` and run `uv sync --extra dev` on the platform you are using, or keep separate clones per OS.
