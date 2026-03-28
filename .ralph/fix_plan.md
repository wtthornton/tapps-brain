# Ralph Fix Plan — tapps-brain

**Scope:** Housekeeping and quality gates (below) are historical/completed. **Current feature delivery** follows the open-issues roadmap (`docs/planning/open-issues-roadmap.md`) — work items **in priority order** in that section. EPIC-032 and DEPLOY-OPENCLAW remain deferred unless the roadmap references them.

**Not packaged:** This file and all of `.ralph/` are **Ralph / dev-loop only** — they are not part of the PyPI wheel or OpenClaw artifacts. **Canonical delivery status** for the product lives in `docs/planning/open-issues-roadmap.md`. Non-Ralph agents should update the roadmap (and GitHub), not this file, unless explicitly syncing for Ralph. See [Open issues roadmap vs Ralph tooling](../docs/planning/PLANNING.md#open-issues-roadmap-vs-ralph-tooling).

**Task sizing:** Each item is scoped to ONE Ralph loop (~15 min). Do one, check it off, commit.

## Completed Epics

- EPIC-001 through EPIC-016 (core features, test hardening)
- BUG-001: Pre-review critical fixes (7 bugs)
- BUG-002: Source trust regression & uncommitted WIP (6 tasks)
- EPIC-017 through EPIC-025: Code review cycle (53 tasks)
- EPIC-026: OpenClaw Memory Replacement (6 tasks)
- EPIC-027: OpenClaw Full Feature Surface — 54 MCP tools (9 tasks; surface grew post-027)
- EPIC-028: OpenClaw Plugin Hardening (9 tasks)
- EPIC-029: Feedback Collection (explicit + implicit signals, MCP/CLI, Hive propagation)
- EPIC-030: Diagnostics & Self-Monitoring (scorecard, EWMA, circuit breaker, MCP/CLI)
- EPIC-031: Continuous Improvement Flywheel (evaluation harness, Bayesian confidence, gaps, reports, MCP/CLI)
- EPIC-033: OpenClaw Plugin SDK Alignment (GitHub #4–#7)
- EPIC-034: Production readiness QA remediation (lint, format, mypy, plugin tests)
- EPIC-035: OpenClaw install/upgrade UX consistency (docs + runbook)
- EPIC-036: Release gate hardening (`release-ready.sh`, docs checker, CI)

## Next Tasks

---

### OPEN-ISSUES-ROADMAP: GitHub delivery priority

**Source of truth:** `docs/planning/open-issues-roadmap.md` (last updated: 2026-03-28).  
**Legend:** `not_started` | `in_progress` | `blocked` | `done` | `closed`

Do **one unchecked item at a time** in the order below (do not skip ahead for lower-priority issues).

| Order | Issue | Title | Status | Notes |
|---:|---:|---|---|---|
| 1 | #30 | sqlite-vec integration | `done` | Maps to EPIC-040 **040.9** — optional dep, `memory_vec`, RRF with BM25 |
| 2 | #15 | Diagnostics health command + MCP | `done` | CLI + MCP + store health sqlite-vec fields |
| 3 | #45 | Profile-driven agent onboarding | `done` | `profile onboard` + `memory_profile_onboarding` |
| 4 | #12 | Hive pub-sub / push notifications | `done` | Revision + `hive watch` + MCP poll; **GitHub closed** 2026-03-28 |
| 5 | #23 | SQLCipher at-rest encryption | `done` | Optional `[encryption]` extra, migrate CLI, `docs/guides/sqlcipher.md`; **GitHub closed** 2026-03-28 |
| 6 | #19 | Sub-agent memory relay | `done` | `memory_relay` module; CLI `relay import`; MCP `tapps_brain_relay_export`; docs/guides/memory-relay.md |
| 7 | #40 | Adaptive hybrid fusion | `done` | Maps to EPIC-040 **040.10** — `hybrid_rrf_weights_for_query` + weighted RRF (2026-03-28) |
| 8 | #18 | Hive push / push-tagged | `done` | CLI `hive push` / `push-tagged`, MCP `hive_push` (2026-03-28) |
| 9 | #21 | Store stale listing | `not_started` | List stale entries; machine-readable output |
| 10 | #20 | Profile tier migration | `not_started` | Safe tier remap; audit + dry-run |
| 11 | #17 | Session summarization workflow | `done` | `session end`, `session_summary.py`, MCP `tapps_brain_session_end`; **GitHub closed** 2026-03-28 |

**Checklist (mirror roadmap):**

- [x] **OR-1** GitHub **#30** sqlite-vec (`done` — 2026-03-27)
- [x] **OR-2** GitHub **#15** diagnostics health (`done` — validate on GitHub; CLI + MCP + health fields)
- [x] **OR-3** GitHub **#45** profile-driven onboarding (`done` — CLI + MCP)
- [x] **OR-4** GitHub **#12** hive pub-sub (`done` — 2026-03-27)
- [x] **OR-5** GitHub **#23** SQLCipher (`done` — 2026-03-27)
- [x] **OR-6** GitHub **#19** sub-agent memory relay (`done`)
- [x] **OR-7** GitHub **#40** adaptive hybrid fusion (`done` in repo — 2026-03-28; close on GitHub when verified)
- [x] **OR-8** GitHub **#18** hive push / push-tagged (`done` in repo — 2026-03-28; close on GitHub when verified)
- [ ] **OR-9** GitHub **#21** store stale (`not_started`)
- [ ] **OR-10** GitHub **#20** profile migrate (`not_started`)
- [x] **OR-11** GitHub **#17** session summarization (`done` / closed on GitHub — 2026-03-28)

---

### HOUSEKEEPING-001: Close resolved GitHub issues

**Priority: HIGH — public issue tracker shows bugs that are already fixed**

- [x] **HK-001.1** Close GitHub issues #4, #5, #6: these were fixed by EPIC-033 (commits reference STORY-033.2, 033.3, 033.1). Close each with a comment linking to the fixing commit.

---

### HOUSEKEEPING-002: Update stale planning docs

**Priority: MEDIUM — keep planning artifacts aligned with shipped work**

- [x] **HK-002.1** Update `docs/planning/STATUS.md`: mark EPIC-017 through EPIC-025 as `done`, mark EPIC-029/030/031/033 with completion dates, update epics summary table, verify current focus section reflects reality.
- [x] **HK-002.2** Update `docs/planning/PLANNING.md` epic directory listing: EPIC-026 through EPIC-036 marked with correct done/planned annotations (verified 2026-03-24).

---

### QUALITY-001: Full QA gate

**Priority: MEDIUM — verify project health after all recent changes**

- [x] **QA-001.1** Run full test suite: `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`. Fix any failures. *(2026-03-24: 2341 passed, 3 skipped, 7 deselected; coverage 95.16%; Windows / Python 3.13.)*
- [x] **QA-001.2** Run lint + format: `ruff check src/ tests/ && ruff format --check src/ tests/`. Fix any violations. *(2026-03-24: pass.)*
- [x] **QA-001.3** Run type check: `mypy --strict src/tapps_brain/`. Fix any errors. *(2026-03-24: pass.)*

---

### READY-034: Production readiness QA remediation (EPIC-034)

**Priority: CRITICAL — hard blockers found in readiness review**

- [x] **R34-001.1** Ruff baseline cleanup: fix current lint violations and formatting drift in touched files until `ruff check src/ tests/` and `ruff format --check src/ tests/` pass.
- [x] **R34-001.2** Stabilize OpenClaw plugin test runner: fix unhandled rejection / timeout cleanup path so `cd openclaw-plugin && npm test` exits 0 without unhandled errors.
- [x] **R34-001.3** Strict typing pass path: make `mypy --strict src/tapps_brain/` reliably pass in the supported environment; document environment nuance if needed.
- [x] **R34-001.4** Full QA evidence run: execute tests + lint/format + mypy + plugin tests in one release-candidate runbook, fix any failures, and record outcome in status docs.

---

### READY-035: OpenClaw install/upgrade UX consistency (EPIC-035)

**Priority: HIGH — operator-facing docs currently inconsistent**

- [x] **R35-001.1** Normalize OpenClaw install command docs: choose and apply one canonical install command form across `docs/guides/openclaw*.md`, `openclaw-plugin/README.md`, `openclaw-plugin/UPGRADING.md`, and `openclaw-skill/SKILL.md`.
- [x] **R35-001.2** Reconcile capability/status claims: remove planned-vs-shipped contradictions and align tool/resource counts + compatibility messaging across OpenClaw docs.
- [x] **R35-001.3** Publish canonical OpenClaw runbook: create/refresh one source-of-truth install + upgrade flow (PyPI and Git-only), with explicit verify + restart steps and cross-links.

---

### READY-036: Release gate hardening (EPIC-036)

**Priority: HIGH — enforce production readiness continuously**

- [x] **R36-001.1** Add release-ready gate script: create a single command/script that runs packaging build, version-consistency test, full QA checks, and OpenClaw plugin build/test with fail-fast behavior.
- [x] **R36-001.2** Add docs consistency checker: implement a script to detect OpenClaw command/status/tool-count drift in docs and fail on inconsistency.
- [x] **R36-001.3** Wire gate into CI + checklist: integrate the release-ready gate and docs checker into CI workflow(s), `scripts/publish-checklist.md`, and `docs/planning/STATUS.md` references.

---

## EPIC-037: OpenClaw Plugin SDK Realignment

**Priority: CRITICAL — plugin cannot load in a real OpenClaw installation**

- [x] **037.1** Rewrite `openclaw-sdk.d.ts` with real SDK types verified against github.com/openclaw/openclaw source. *(2026-03-23)*
- [x] **037.2** Fix `resolveAgentWorkspaceDir(api.config, agentId)`, switch to `api.pluginConfig`, remove compat modes/shim/tool groups. *(2026-03-23)*
- [x] **037.3** Rewrite all tool registration functions to use `registerTool(toolObject)` with `createMcpProxyTool` DRY helper. *(2026-03-23)*
- [x] **037.4** Fix `definePluginEntry` (description, kind), parameterless `registerContextEngine` factory, `delegateCompactionToRuntime` in compact. *(2026-03-23)*

---

## EPIC-038: OpenClaw Plugin Simplification

**Priority: HIGH — remove ~200 lines of dead compat code after 037 lands**

- [x] **038.1** Removed version detection, compat modes, 3-mode branching. `register()` calls `registerContextEngine` directly. *(2026-03-23)*
- [x] **038.2** Removed `definePluginEntry` shim. Static import from `openclaw/plugin-sdk/plugin-entry`. *(2026-03-23)*
- [x] **038.3** Compact delegates to `delegateCompactionToRuntime` after flushing messages. *(2026-03-23)*
- [x] **038.4** Removed `isGroupEnabled()` and `toolGroups` config. All tools registered unconditionally. *(2026-03-23)*
- [x] **038.5** Tests updated: removed version compat tests (30 tests), fixed compact/bootstrap assertions, added SDK mocks. 101 tests pass. *(2026-03-23)*

---

---

## EPIC-040: tapps-brain v2.0 — Research-Driven Upgrades (GitHub #24–#44)

**Priority: CRITICAL — 21 stories from competitive research + algorithm analysis**
**Goal: Make tapps-brain the best AI agent memory system that exists**
**Constraint: ALL changes must be backward-compatible with existing profiles**
**Research reports: plans/tapps-brain-research-2026-03-25.md, plans/tapps-brain-algorithms-research-2026-03-25.md**

### Phase 1: Quick Wins (no dependency, low risk)

- [x] **040.1** BM25+ variant (GitHub #34): In `bm25.py` `_score_doc()`, add lower-bound delta δ=1 to TF-saturation score. One-line change. Update tests.
- [x] **040.2** Provenance metadata (GitHub #38): Add `source_session_id TEXT`, `source_channel TEXT`, `source_message_id TEXT`, `triggered_by TEXT` columns to memories table via migration. Update `save()` to accept and persist these fields. Update schema version.
- [x] **040.3** Temporal fact validity (GitHub #29): Add `valid_from TEXT`, `valid_until TEXT`, `superseded_by TEXT` columns to memories table via migration. Update query layer to filter expired facts by default (`WHERE valid_until IS NULL OR valid_until > datetime('now')`). Add `include_historical` parameter to search/recall. Update schema version.
- [x] **040.4** Memory health stats (GitHub #43): Add `tapps-brain stats` CLI command showing: total by tier with avg confidence, added this week, decayed/prunable, near-expiry, top accessed. Expose via MCP tool.

### Phase 2: Core Algorithm Upgrades

- [x] **040.5** Adaptive stability schema (GitHub #28): Add `stability REAL` and `difficulty REAL` columns to memories table. Add `adaptive_stability` boolean to `LayerDefinition`. Default false for backward compat. Implement FSRS-style stability update on reinforcement when enabled. Update `DecayConfig` and `calculate_decayed_confidence()`.
- [x] **040.6** Bayesian confidence (GitHub #35): Add `useful_access_count INTEGER DEFAULT 0` and `total_access_count INTEGER DEFAULT 0` columns. Implement `confidence_new = confidence_old × (useful + α) / (total + α + β)` update path. Track useful vs total access in retrieval feedback loop.
- [x] **040.7** Stability-based promotion (GitHub #39): Add `promotion_strategy` field to `LayerDefinition` (default: "threshold" for backward compat). Implement "stability" strategy: `promote_score = stability × log1p(access_count) × (1 - D/10)`. Depends on 040.5.
- [x] **040.8** Enhanced composite scoring (GitHub #41): Add optional `graph_centrality` (default 0.0) and `provenance_trust` (default 0.0) weight fields to `ScoringConfig`. If both are 0.0, existing 4-weight formula unchanged. Update `_weights_sum_check()`. Depends on 040.2.

### Phase 3: Search & Retrieval

- [x] **040.9** sqlite-vec integration (GitHub #30): Add sqlite-vec as optional dependency. Create `memory_vec` virtual table for vector embeddings. Implement local embedding via all-MiniLM-L6-v2 ONNX (optional dep). Compute embeddings on write when available. Unify with BM25 via existing RRF fusion. **Roadmap:** open-issues **OR-1** / priority 1. *(2026-03-27)*
- [x] **040.10** Adaptive hybrid fusion (GitHub #40): Query-type heuristics (`hybrid_rrf_weights_for_query`) and weighted RRF (`reciprocal_rank_fusion_weighted`); `hybrid_config.adaptive_fusion=False` restores 1:1 weights. Depends on 040.9. **Roadmap:** open-issues **OR-7** (`done` in repo — 2026-03-28).
- [x] **040.11** YAKE/RAKE key generation (GitHub #42): Implement RAKE algorithm (~50 lines pure Python) for automatic memory key generation from text. Use in extraction.py and session summarization.

### Phase 4: Consolidation & Summarization

- [x] **040.12** TextRank summarization (GitHub #32): Implement Mihalcea & Tarau 2004 TextRank for extractive summarization. Pure Python, no dependencies. ~100 lines. Use for session summarization and dispose() flush. *(2026-03-25)*
- [x] **040.13** Louvain consolidation (GitHub #36): Replace greedy clustering in `similarity.py` with Louvain community detection (use `python-louvain` or pure implementation). Add information-theoretic merge criterion.
- [x] **040.14** Write deduplication with Bloom filter (GitHub #31): Implement in-memory Bloom filter (64KB, k=7). Check before every write. If possible duplicate, run Jaccard similarity check. If dup found, reinforce existing instead of inserting.

### Phase 5: Graph & Relationships

- [x] **040.15** Memory relationship graph + PageRank (GitHub #33): Enhance `memory_relations` table. Implement PageRank scoring via recursive CTE or in-memory computation. Pre-compute on write/consolidation. Add multi-hop traversal query support.
- [x] **040.16** Per-entry conflict detection (GitHub #44): On save, check for semantic contradiction with existing entries in same tier. Surface conflicts with resolution options (override with temporal validity from 040.3, keep both, reject). Depends on 040.3.

### Phase 6: OpenClaw Plugin Fixes

- [x] **040.17** dispose() flush (GitHub #24): In OpenClaw plugin `dispose()`, flush `recentMessages` to tapps-brain via `memory_ingest` before stopping MCP client. Reuse compact() flush logic.
- [x] **040.18** Periodic mid-session flush (GitHub #25): Add configurable `flushIntervalMessages` (default 10) to plugin config. In `ingest()`, flush to tapps-brain after accumulating N messages.
- [x] **040.19** assemble() recall nudge (GitHub #27): Inject memory-recall reminder in assemble() context block when incoming message looks like a question or references prior context.
- [x] **040.20** openclaw init/upgrade command (GitHub #26): Add `tapps-brain openclaw init` (scaffold correct workspace files) and `tapps-brain openclaw upgrade` (fix stale AGENTS.md, regenerate MEMORY.md from tapps-brain, migrate tier names).

### Phase 7: Multi-Agent Architecture

- [x] **040.21** Groups as first-class layer (GitHub #37): Add `hive_groups` and `hive_group_members` tables. Add group-scoped memory storage (namespace = group name). Update recall path: brain → group(s) → hive universal. Add CLI/MCP tools for group management. Preserve profile sovereignty per brain. *(2026-03-25)*

### Release

- [x] **040.22** Version bump to v2.0.0, update CHANGELOG.md, run full QA gate, rebuild OpenClaw plugin, update docs.

---

## Deferred (not in open-issues roadmap scope)

| Epic | Title | Priority | Notes |
|------|-------|----------|-------|
| EPIC-032 | OTel GenAI Semantic Conventions | LOW | 6 tasks, optional observability upgrade |
| DEPLOY-OPENCLAW | PyPI publish + ClawHub listing | — | 8 tasks, distribution/packaging |
