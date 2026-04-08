# Project status snapshot

**Last updated:** 2026-04-05 (America/Chicago) — **v2.0.4** (EPIC-052 full codebase review sweep: write-through rollback fixes on `reinforce`/`record_access`, validator + docstring + CLI exit-code + README badge hygiene; 2892 tests green); **EPIC-041** done; **#51**–**#64** closed; **EPIC-042** stories **042.1–042.8** done (epic success criteria / eval note still in `EPIC-042.md`); **EPIC-050** partial (050.3 + WAL checkpoint runbook); **EPIC-044** **044.1**–**044.2** RAG safety + Bloom dedup, **044.3** save-path conflicts + **offline** export (`run_save_conflict_candidate_report`, CLI `maintenance save-conflict-candidates`, [`save-conflict-nli-offline.md`](../guides/save-conflict-nli-offline.md); async/NLI product wiring still optional), **044.4** audit + threshold sweep + **merge undo** (`undo_consolidation_merge`, CLI `maintenance consolidation-merge-undo`, `consolidation_merge_undo` audit) + consolidated save **`skip_consolidation=True`**, **044.5** GC dry-run/metrics/`archive.jsonl`, **044.6** `seeding.seed_version` + `profile_seed_version` on health/stats/native health, **044.7** global + optional **`limits.max_entries_per_group`** eviction (health / stats / CLI); **EPIC-051** **done** — §10 checklist ADRs **001**–**006** in [`adr/`](adr/) ([`EPIC-051.md`](epics/EPIC-051.md)); **next-session handoff:** [`next-session-prompt.md`](next-session-prompt.md)

**Package version (PyPI / `pyproject.toml`):** **2.0.4**

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
| EPIC-040 | tapps-brain v2.0 — research-driven upgrades | active | — (major v2.0 stories shipped; see `epics/EPIC-040.md`, `open-issues-roadmap.md`) |
| EPIC-041 | Federation hub `memory_group`, Hive `group:<name>`, health/guides | done | 2026-04-02 — **#52** checklist closed on GitHub; **#51**/**#63**/**#64** closed |
| EPIC-052 | Full Codebase Code Review — 2026-Q2 Sweep | done | 2026-04-05 — all 18 stories closed; 6 fixes landed in v2.0.4 ([`EPIC-052.md`](epics/EPIC-052.md)) |
| EPIC-042 … EPIC-051 | Feature / technology improvement program | **051 done**; 050 partial; 042 story grid done; 044 mostly shipped | **EPIC-051** **2026-04-03** — [`EPIC-051.md`](epics/EPIC-051.md); ADR **001**–**006** in [`adr/`](adr/) (§10 checklist + [`sqlcipher.md`](../guides/sqlcipher.md) ops for **051.5**). **EPIC-042** — **042.1**–**042.8** done. **EPIC-044** — **044.1**/**044.2**/**044.4**/**044.5**/**044.6**/**044.7** + **044.3** core/offline export. **EPIC-050** — **050.2**/**050.3** + WAL checkpoint note; **050.1** doc done. Index: `epics/EPIC-042-feature-tech-index.md` |

## Current focus

**Shipped:** EPIC-040 bulk delivery (v2.0.x; **2.0.3** version alignment; **2.0.2** agent-integration + relay docs; **2.0.1** OpenClaw MCP unwrap + tier normalization), optional **SQLCipher** (`[encryption]` extra, GitHub **#23**), **sub-agent memory relay** (GitHub **#19**), adaptive hybrid fusion (**#40**), hive push (**#18**), maintenance stale / profile tier migrate (**#21**, **#20**), OpenClaw **#46** / **#48** / mitigated **#47**, and **#49** v1 project-local **`memory_group`** (schema **v16**, MCP/CLI, docs — GitHub **#49** **closed** 2026-03-29). MCP server tool/resource **counts** and URI list: `docs/generated/mcp-tools-manifest.json` (source: `mcp_server.py`). OpenClaw plugin uses the official `@modelcontextprotocol/sdk` transport (EPIC-039). **Recent `main` (through 2026-04-03):** [`embedding-model-card.md`](../guides/embedding-model-card.md) (includes **§ Performance review backlog** for deferred dense-path / save-path ideas); optional `scoring.relevance_normalization: minmax`; **STORY-042.4** — RRF formula + citations in `fusion.py`, `profile.hybrid_fusion` / `HybridFusionConfig` (`top_k_lexical` / `top_k_dense`, `rrf_k`) wired through `inject_memories`; **STORY-042.3** — [`sqlite-vec-operators.md`](../guides/sqlite-vec-operators.md); **STORY-042.6** — `memory_rerank` / `reranker_failed_fallback_to_original` structured logs, `MemoryRetriever.last_rerank_stats`, `inject_memories` `rerank_*` telemetry; opt-in `TAPPS_SQLITE_MEMORY_READONLY_SEARCH` read connection for FTS + sqlite-vec KNN; **WAL checkpoint** operator note for long-lived MCP (`sqlite-database-locked.md`, `openclaw-runbook.md`); **STORY-044.1** — `profile.safety` / `SafetyConfig`, `rag_safety.*` metrics, health `rag_safety_*`, injection sanitised path; **STORY-044.2** — `normalize_for_dedup` NFKC, Bloom FP helpers + docs; **STORY-044.4** — `consolidation_merge` / `consolidation_source` / `consolidation_merge_undo` audit; `undo_consolidation_merge` + CLI `maintenance consolidation-merge-undo`; consolidated save `skip_consolidation=True`; `evaluation.run_consolidation_threshold_sweep` + CLI `maintenance consolidation-threshold-sweep`; **EPIC-044.3** — save-time conflicts: `exclude_key`; invalidated rows get `contradicted` + deterministic `contradiction_reason`; `profile.conflict_check` (`ConflictCheckConfig` aggressiveness or `similarity_threshold`); `detect_save_conflicts` → `SaveConflictHit` list; offline `run_save_conflict_candidate_report` + CLI `maintenance save-conflict-candidates` + [`save-conflict-nli-offline.md`](../guides/save-conflict-nli-offline.md); **STORY-044.5** — GC `GCResult` dry-run reason counts + `store.gc.archive_bytes`, health `gc_*`, CLI/MCP via `MemoryStore.gc`, canonical `archive.jsonl`; **STORY-044.6** — `MemoryProfile.seeding.seed_version`, `profile_seed_version` in seed summaries and on `StoreHealthReport` / `maintenance health` / `run_health_check` / `memory://stats`; **STORY-044.7** — eviction policy + optional **`limits.max_entries_per_group`** in [`data-stores-and-schema.md`](../engineering/data-stores-and-schema.md). **Schema v17** + int8 quantization spike helpers + `embedding_model_id` on embed path (**STORY-042.2** done). Concurrent save stress test wall-clock bound **60s** for stable full-suite runs on loaded Windows hosts.

**Next-session prompt (copy-paste for agents):** [`next-session-prompt.md`](next-session-prompt.md).

**Next (canonical queue: [`open-issues-roadmap.md`](open-issues-roadmap.md); Ralph mirror: `.ralph/fix_plan.md` OPEN-ISSUES):**
1. **Gating:** **Further** save-path metrics **beyond** [`ADR-006`](adr/ADR-006-save-path-observability.md) (histograms + `save_phase_summary` + `memory://metrics`), **EPIC-042** hygiene, and in-product **NLI/async** conflict wiring are **backlogged by default** — see [`PLANNING.md` § Optional backlog gating](PLANNING.md#optional-backlog-gating) for triggers (a)–(c).
2. **EPIC-044** — **044.1**–**044.7** shipped on `main` (including **044.3** core + offline export); optional product NLI only with trigger (c).
3. **EPIC-050** — lock-scope reduction still deferred unless benchmark-driven; async wrapper (`tapps_brain_async`) optional spike only.
4. **EPIC-032** — OTel GenAI semantic conventions (deferred).

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
