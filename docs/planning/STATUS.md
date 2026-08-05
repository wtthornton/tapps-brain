# Project status snapshot

**Last updated:** 2026-08-05 — **v3.29.0**. Delivery via [Linear project](https://linear.app/tappscodingagents/project/tapps-brain-e5604347c7db) only.

**Package version (`pyproject.toml`):** **3.29.0**

Human-readable snapshot of the repo. **Canonical queue:** [tapps-brain Linear project](https://linear.app/tappscodingagents/project/tapps-brain-e5604347c7db). Epic acceptance criteria: [`epics/`](./epics/).

## Feature intake standard

- All new `feat` proposals must follow [`FEATURE_FEASIBILITY_CRITERIA.md`](./FEATURE_FEASIBILITY_CRITERIA.md).
- Agent enforcement rules: [`AGENT_FEATURE_GOVERNANCE.md`](./AGENT_FEATURE_GOVERNANCE.md).
- Use the required scorecard + hard gates before opening or planning a feature issue.
- Proposals that skip this process are treated as incomplete and should be re-scoped, deferred, or rejected.
- Triage label filters and optional Projects setup: [`ISSUE_TRIAGE_VIEWS.md`](./ISSUE_TRIAGE_VIEWS.md).

## Quality gates

| Check | Target | Notes |
|--------|--------|--------|
| Tests | ~2940+ collected (`pytest tests/`) | Benchmarks excluded in CI-style runs via `-m "not benchmark"` |
| Coverage | ≥ 95% | `tapps_brain` package (`--cov-fail-under=95`) |
| Lint / format | clean | `ruff check`, `ruff format --check` |
| Types | strict | `mypy --strict src/tapps_brain/` |
| Release gate | green before publish | `bash scripts/release-ready.sh` (WSL/Git Bash on Windows); CI job `release-ready` |

## Storage / schema

All durable stores are **PostgreSQL** (ADR-007; SQLite removed in v3.4.0). Schema managed by versioned migrations in `src/tapps_brain/migrations/private/` and `src/tapps_brain/migrations/` (Hive/Federation).

- **Migration 001:** initial private schema (`private_memories`, `archived_memories`, `session_chunks`).
- **Migration 002:** pgvector HNSW index (`m=16, ef_construction=200`, cosine ops).
- **Migration 003:** feedback + session tables.
- **Migration 004:** diagnostics history table.
- **Migration 005:** `audit_log` table (replaces `memory_log.jsonl`).
- **Migration 006:** GC archive table.
- **Migrations 007–029:** flywheel meta, project profiles, RLS, idempotency keys, per-tenant auth, KG tables (`016`–`021`), partitioned `experience_events` (`020`/`022`), experience query index (`023`), profile-scoped learned KV (`024`), KG entities tenant unique (`025`), renumbered `memory_class`/`memory_status` (`026`/`027`), document plane (`028`), idempotency keys scoped by operation (`029`). Current max private migration: **029**.
- **Federation:** PostgreSQL (`TAPPS_BRAIN_FEDERATION_DSN`) — `federated_memories` carries optional publisher `memory_group` (GitHub **#51** / EPIC-041); see `docs/guides/federation.md`.
- **Hive:** PostgreSQL (`TAPPS_BRAIN_HIVE_DSN`) — pgvector + tsvector + `LISTEN/NOTIFY`; namespace-aware schema.

## Dependencies (high level)

- **Runtime (core):** `pydantic`, `structlog`, `pyyaml`, `psycopg[binary,pool]`, `opentelemetry-api` — no typer/mcp in core.
- **Extras:** `[cli]` adds `typer`; `[mcp]` adds `mcp`; `[reranker]` adds `flashrank`; `[otel]` adds `opentelemetry-sdk`; `[visual]` adds `playwright`; `[all]` = `cli + mcp + reranker`.
- **Optional:** `anthropic_sdk` and `openai_sdk` for LLM-as-judge evaluation.
- **Dev:** test stack + `mcp` so MCP unit tests run under `uv sync --group dev`.

Install for contributors:

```bash
uv sync --group dev    # pytest, ruff, mypy, and mcp (needed for MCP unit tests)
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
| EPIC-012 | Plugin Integration | done | 2026-03-21 |
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
| EPIC-026 | Memory Replacement | done | 2026-03-23 |
| EPIC-027 | Full Feature Surface — MCP tools (64 as of 2026-03-29) | done | 2026-03-23 |
| EPIC-028 | Plugin Hardening | done | 2026-03-23 |
| EPIC-029 | Feedback Collection | done | 2026-03-23 |
| EPIC-030 | Diagnostics & Self-Monitoring | done | 2026-03-23 |
| EPIC-031 | Continuous Improvement Flywheel | done | 2026-03-23 |
| EPIC-032 | OTel GenAI semantic conventions | done | 2026-04-27 — [TAP-807](https://linear.app/tappscodingagents/issue/TAP-807) |
| EPIC-033 | Plugin SDK Alignment | done | 2026-03-23 |
| EPIC-034 | Production readiness QA remediation | done | 2026-03-24 |
| EPIC-035 | Install and upgrade UX consistency | done | 2026-03-24 |
| EPIC-036 | Release gate hardening for distribution | done | 2026-03-24 |
| EPIC-037 | Plugin SDK realignment — fix API contract | done | 2026-03-23 |
| EPIC-038 | Plugin simplification — remove dead compat layers | done | 2026-03-23 |
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
| EPIC-059 | PostgreSQL-only persistence plane — SQLite rip-out | done | 2026-04-11 — ADR-007 extended; stage 2 complete |
| EPIC-060 | Engineering documentation — architecture + exceptions | done | 2026-04-27 — [TAP-801](https://linear.app/tappscodingagents/issue/TAP-801) |
| EPIC-061 | Operator runbook | done | 2026-04-27 — [TAP-802](https://linear.app/tappscodingagents/issue/TAP-802) |
| EPIC-062 | Environment contract and README | done | 2026-04-27 |
| EPIC-063 | (internal) | done | — |
| EPIC-064 | NLT brand tokens + motion system + IA foundation | done | 2026-04-15 |
| EPIC-065 | Live dashboard — /snapshot endpoint + panels | done | 2026-04-27 — [TAP-804](https://linear.app/tappscodingagents/issue/TAP-804) |
| EPIC-066 | Postgres-only persistence — production readiness | done | 2026-04-14+ — [TAP-803](https://linear.app/tappscodingagents/issue/TAP-803) |
| EPIC-067 | AsyncMemoryStore (aio.py) | done | 2026-04-14 |
| EPIC-068 | Multi-page brain-visual dashboard (hash router, 6 pages, NLT brand) | done | 2026-04-15 — [TAP-470](https://linear.app/tappscodingagents/issue/TAP-470) |
| EPIC-069 | Multi-tenant project_id on the wire + Postgres profile registry | done | 2026-04-14 — ADR-010 |
| EPIC-070 | Streamable HTTP + FastAPI adapter + service layer | done | 2026-04-14 — v3.5.x; commit f182700 |
| EPIC-071 | TappsBrainClient SDK hardening + async lifecycle + client guide | done | 2026-05-18 — [TAP-2133](https://linear.app/tappscodingagents/issue/TAP-2133) |
| EPIC-072 | Async-native Postgres core (psycopg3 AsyncConnectionPool) | done | 2026-05-07 — [TAP-806](https://linear.app/tappscodingagents/issue/TAP-806) |
| EPIC-073 | Per-profile MCP tool filtering | done | 2026-04-20 — [TAP-563](https://linear.app/tappscodingagents/issue/TAP-563); Phase 2/3 rollout is ops (see EPIC-073.md) |
| EPIC-074 | Experience event query API | done | 2026-06-09 — [TAP-3155](https://linear.app/tappscodingagents/issue/TAP-3155) |
| EPIC-075 | Profile-scoped learned data KV | done | 2026-06-09 — [TAP-3156](https://linear.app/tappscodingagents/issue/TAP-3156) |
| EPIC-077 | Retire autonomous loop (Ralph) | done | 2026-06-09 — [TAP-3198](https://linear.app/tappscodingagents/issue/TAP-3198) |

## Current focus

**Shipped in v3.29.0 (2026-08-05):**
- **Per-save supersede scoping** — `supersede: "global" | "key-scoped"` on `/v1/remember`, `/v1/remember:batch` and MCP `brain_remember`. Default stays `"global"`; `"key-scoped"` declares a key-space of independent facts so one save cannot evict topically adjacent siblings.
- **Write-only Bloom filter removed** ([TAP-5629](https://linear.app/tappscodingagents/issue/TAP-5629)) — `bloom.py` → `dedup.py`; **`bloom_saturation` is gone from `get_metrics()`, `/metrics` and `/snapshot`** — drop any alert or dashboard panel bound to it.
- **Bounded durable merge** ([TAP-5633](https://linear.app/tappscodingagents/issue/TAP-5633)) — concurrent saves could push `count()` permanently past `max_entries`; reproduces only against live Postgres.
- **Release gate now runs against HEAD, not the working tree** — a new stage 0 fails when release-critical paths differ from HEAD. `ALLOW_DIRTY_TREE=1` skips it for local iteration.

**Recently shipped (v3.25–v3.28):** `web_research` / `research_fetch` MCP tools (3.28.0, [TAP-5364](https://linear.app/tappscodingagents/issue/TAP-5364)); per-operation idempotency keys + migration 029 (3.28.1, [TAP-5444](https://linear.app/tappscodingagents/issue/TAP-5444)); save-path dedup and revive fixes plus the `coalesced` / `invalidated` response contract (3.28.3, [TAP-5615](https://linear.app/tappscodingagents/issue/TAP-5615) / [TAP-5616](https://linear.app/tappscodingagents/issue/TAP-5616) / [TAP-5617](https://linear.app/tappscodingagents/issue/TAP-5617)).

**Next-session prompt (copy-paste for agents):** [`next-session-prompt.md`](next-session-prompt.md).

**Queue (2026-08-05):** [EPIC TAP-5459](https://linear.app/tappscodingagents/issue/TAP-5459) — DB-dependent integration tests that CI skips rather than runs — is the open critical path; file new work via `linear-issue` per [`FEATURE_FEASIBILITY_CRITERIA.md`](./FEATURE_FEASIBILITY_CRITERIA.md).

**Operational (see [`EPIC-073.md`](epics/EPIC-073.md) rollout plan):**
1. ~~Opt-in `X-Brain-Profile: coder` in this repo's MCP client config.~~ **Done (2026-06-09)** — `.mcp.json` + `.cursor/mcp.json`.
2. Monitor `mcp_profile_resolution_source_total` before flipping `TAPPS_BRAIN_DEFAULT_PROFILE=coder`.

**Backlog gating (execute only on trigger):** Save-path observability, EPIC-042 eval hygiene, NLI/async conflict wiring — see [`PLANNING.md` § Optional backlog gating](PLANNING.md#optional-backlog-gating).

The legacy [`open-issues-roadmap.md`](open-issues-roadmap.md) was retired to a pointer on 2026-04-21.

## READY-036 release gate (2026-03-24)

- **Script:** `scripts/release-ready.sh` — fail-fast packaging, version tests, pytest (optional skip via `SKIP_FULL_PYTEST=1`), ruff, mypy.
- **CI:** `.github/workflows/ci.yml` — `release-ready` job runs the shell gate with `SKIP_FULL_PYTEST=1` after the test matrix.
- **Remediation on failure:** `scripts/publish-checklist.md`, `docs/planning/epics/EPIC-036.md`.
- **Documented in:** root `README.md`, `CLAUDE.md`, `.cursor/rules/project.mdc`, `docs/guides/mcp.md`, `docs/guides/getting-started.md`, `docs/planning/PLANNING.md`, `CHANGELOG.md` (v2.0.3+).

## READY-035 docs consistency evidence (2026-03-24)

- Capability/status claims reconciled:
  - resource URIs: canonical list in `docs/generated/mcp-tools-manifest.json` (**8** resources, including `memory://agent-contract`; older copy said 7 before that URI shipped)

## READY-034 QA evidence (2026-03-24)

- Re-verified after planning-doc sync: full pytest + ruff + mypy green on Windows (Python 3.13); same counts as below.
- `ruff check src/ tests/` -> pass.
- `ruff format --check src/ tests/` -> pass.
- `mypy --strict src/tapps_brain/` -> pass.
- Full release-candidate runbook executed in one command:
  - `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`
  - `ruff check src/ tests/`
  - `ruff format --check src/ tests/`
  - `mypy --strict src/tapps_brain/`
- Outcome: pass (`2341 passed, 3 skipped, 7 deselected`, coverage `95.16%`).

## WSL / Windows

- Full test runs are **WSL-first** (bash, Linux `.venv`). See **`CLAUDE.md`** for WSL notes.
- In WSL, activate with `source .venv/bin/activate` (not `Scripts/activate`).
- **One checkout, one OS for `.venv`:** alternating `uv sync` on the same tree between WSL (Linux layout) and native Windows can leave `.venv` in a state where `uv` fails to replace `lib64` (access denied). Remove `.venv` and run `uv sync --group dev` on the platform you are using, or keep separate clones per OS.
