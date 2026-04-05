---
id: EPIC-052
title: "Full Codebase Code Review — 2026-Q2 Sweep"
status: planned
priority: medium
created: 2026-04-05
tags: [review, quality, correctness, dead-code, types, performance]
---

# EPIC-052: Full Codebase Code Review — 2026-Q2 Sweep

## Context

The last full code-review sweep completed on 2026-03-23 across **EPIC-017 → EPIC-025**. Since then, substantial code has landed under **EPIC-040 → EPIC-051** (sqlite-vec integration, SQLCipher, adaptive hybrid fusion, hive push, session summarization, RAG safety, Bloom dedup, save-conflict detection, consolidation merge/undo, GC reasons, seeding versioning, per-group caps, ADRs 001–006, save-path observability baseline).

The current tree has ~**30k LOC** across ~**64 modules**. Several files grew substantially (`cli.py` 3371 LOC, `store.py` 2951 LOC, `mcp_server.py` 2453 LOC). A second sweep ensures the foundation stays solid before further feature work.

### Why now

- Test suite is green (`2341 passed`, coverage 95.16%) — a clean baseline for review.
- No open GitHub issues (#65 closed 2026-04-05) — no competing fix work.
- Large modules have accumulated complexity through incremental stories; now is the right time to look for dead code, duplicated logic, and type gaps.
- No new major features are currently in-flight that would churn the reviewed code.

### Non-goals

- **No behavior changes** unless tied to a correctness/security bug found during review.
- **No refactors** for style alone. Structural refactors (e.g., `MemoryStore` decomposition, tracking row 22) remain out of scope — see [`adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md`](../adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md).
- **No new features.** Bug fixes only.

## Success criteria

- [ ] Every module under `src/tapps_brain/` is reviewed exactly once against the checklist below.
- [ ] All findings are either fixed in a story PR (with tests) or filed as a follow-up GitHub issue when out of scope.
- [ ] Test suite stays green after every story: `pytest -m "not benchmark"`, `ruff check`, `ruff format --check`, `mypy --strict`, `openclaw-plugin` tests.
- [ ] Coverage stays **≥ 95%** on `tapps_brain` package.
- [ ] Each story closes with a one-paragraph findings note in its section below (bugs found, fixes landed, follow-ups filed).

## Review checklist (applied per story)

1. **Correctness:** logic bugs, off-by-one, boundary conditions, race conditions, TOCTOU gaps.
2. **Security:** SQL/command injection, unsanitized input at boundaries, credential/PII leaks in logs, path traversal.
3. **Performance:** N+1 queries, unnecessary full scans, missing indexes, quadratic loops, unbounded allocations.
4. **Dead code:** unreachable branches, unused imports/vars/functions, obsolete compatibility shims.
5. **Error handling:** swallowed exceptions, missing validation at trust boundaries, silent failures, over-broad `except`.
6. **Type safety:** `Any` casts, missing `None` checks, `cast()` hiding real bugs, `type: ignore` without comment.
7. **Consistency:** duplicated logic vs existing helper, inconsistent naming, divergent error conventions.
8. **Docs:** stale docstrings / comments that contradict code; misleading function names.

## Stories

Stories are sized for ~1 Ralph loop each (~15–30 min review + targeted fixes). Execute **in order**; later stories may depend on fixes from earlier ones.

Legend: **Effort** S ≤ 15 min, M ≤ 30 min, L ≤ 60 min.

---

### STORY-052.1: Storage — `persistence.py` (schema, migrations, FTS5)

**Status:** not_started | **Effort:** L | **Depends on:** none
**Files:** `src/tapps_brain/persistence.py` (1465 LOC)
**Verification:** `pytest tests/unit/test_memory_persistence.py tests/unit/test_migrations.py -v`

Focus areas:
- Migration idempotency (v1 → v17 forward paths).
- FTS5 triggers: insert/update/delete parity with main table; `contentless` vs `content` table modes.
- PRAGMA set on open (busy_timeout, cache_size, mmap_size, journal_mode=WAL, foreign_keys).
- Parameterization of all SQL; confirm no f-string interpolation of untrusted values.

#### Findings

_(fill on story close)_

---

### STORY-052.2: Storage — `store.py` core CRUD (first half)

**Status:** not_started | **Effort:** L | **Depends on:** 052.1
**Files:** `src/tapps_brain/store.py` lines 1 – ~1500 (2951 LOC total)
**Verification:** `pytest tests/unit/test_store_*.py -v`

Focus areas:
- `save` / `delete` / `update` / `search` / `recall` core paths.
- Lock model: `threading.Lock` usage, transaction boundaries, partial-failure rollback.
- Tier normalization call sites (`tier_normalize.py` integration).
- Audit log emission on every mutating path.

---

### STORY-052.3: Storage — `store.py` advanced (second half)

**Status:** not_started | **Effort:** L | **Depends on:** 052.2
**Files:** `src/tapps_brain/store.py` lines ~1500 – end
**Verification:** `pytest tests/unit/test_store_*.py tests/integration/test_store_*.py -v`

Focus areas:
- GC, consolidation, merge-undo, profile migrations, relations, hive propagation entry points.
- Health / stats assembly (`StoreHealthReport`, `gc_*` fields, `rag_safety_*`, `profile_seed_version`).
- `exclude_key`, save-time conflict detection (`detect_save_conflicts`), `skip_consolidation` flag.

---

### STORY-052.4: Storage — models, integrity, audit, protocols

**Status:** not_started | **Effort:** M | **Depends on:** none
**Files:** `models.py`, `integrity.py`, `audit.py`, `_protocols.py`, `_feature_flags.py`, `agent_scope.py`, `migration.py`
**Verification:** `pytest tests/unit/test_models.py tests/unit/test_integrity.py tests/unit/test_audit.py -v`

Focus areas:
- Pydantic validators: reject malformed input at ingest boundary.
- `integrity_hash` coverage on every mutation path.
- Audit action strings are canonical (no typos, matched by CLI filters).
- Feature flags: dead flags, default values consistent with docs.

---

### STORY-052.5: Retrieval — scoring, fusion, reranker

**Status:** not_started | **Effort:** L | **Depends on:** 052.1
**Files:** `retrieval.py`, `recall.py`, `recall_diagnostics.py`, `fusion.py`, `bm25.py`, `lexical.py`, `similarity.py`, `reranker.py`
**Verification:** `pytest tests/unit/test_retrieval*.py tests/unit/test_fusion*.py tests/unit/test_reranker*.py -v`

Focus areas:
- RRF weight application and `adaptive_fusion=False` passthrough.
- Rerank fallback path (`reranker_failed_fallback_to_original`) preserves original order.
- `top_k_lexical` / `top_k_dense` / `rrf_k` boundary behaviour (0, 1, very large).
- Read-only connection path for search (`TAPPS_SQLITE_MEMORY_READONLY_SEARCH`).

---

### STORY-052.6: Retrieval — embeddings, sqlite-vec, injection

**Status:** not_started | **Effort:** M | **Depends on:** 052.5
**Files:** `embeddings.py`, `sqlite_vec_index.py`, `injection.py`
**Verification:** `pytest tests/unit/test_embeddings.py tests/unit/test_sqlite_vec*.py tests/unit/test_injection.py -v`

Focus areas:
- `embedding_model_id` provenance on every embed path (STORY-042.2 regressions).
- `int8` quantization code paths.
- Injection telemetry (`rerank_*`, citation footer handling).

---

### STORY-052.7: Lifecycle — consolidation, decay, GC, promotion

**Status:** not_started | **Effort:** L | **Depends on:** 052.3
**Files:** `consolidation.py`, `auto_consolidation.py`, `decay.py`, `gc.py`, `reinforcement.py`, `promotion.py`, `contradictions.py`, `seeding.py`, `tier_normalize.py`
**Verification:** `pytest tests/unit/test_consolidation*.py tests/unit/test_gc*.py tests/unit/test_decay*.py -v`

Focus areas:
- `consolidation_merge_undo` audit trail completeness.
- GC `reason_counts` + `archive.jsonl` schema stability.
- FSRS stability/difficulty updates on every recall.
- `skip_consolidation=True` on consolidated saves (no recursion).

---

### STORY-052.8: Safety & validation

**Status:** not_started | **Effort:** M | **Depends on:** none
**Files:** `safety.py`, `doc_validation.py`, `rate_limiter.py`, `bloom.py`, `extraction.py`
**Verification:** `pytest tests/unit/test_safety*.py tests/unit/test_doc_validation.py tests/unit/test_bloom*.py -v`

Focus areas:
- `check_content_safety` ruleset versioning; metrics parity (`rag_safety.blocked/sanitized`).
- Bloom filter false-positive probability bounds; NFKC normalization.
- `doc_validation.py` (945 LOC): look for dead validators, unused rules.
- Rate limiter: window semantics, thread safety.

---

### STORY-052.9: Federation, Hive, relations, relay

**Status:** not_started | **Effort:** L | **Depends on:** 052.3
**Files:** `hive.py` (1251), `federation.py` (866), `relations.py`, `memory_group.py`, `memory_relay.py`
**Verification:** `pytest tests/unit/test_hive*.py tests/unit/test_federation*.py tests/unit/test_relations*.py tests/unit/test_memory_relay*.py -v`

Focus areas:
- `agent_scope` `group:<name>` membership & recall namespace union.
- Federated publisher `memory_group` propagation.
- Relay import: invalid-row handling, `memory_group`/`group` aliasing.
- Hive push: `dry_run`, `bypass_profile_hive_rules`, `hive_write_notify` revision monotonicity.

---

### STORY-052.10: Interfaces — `mcp_server.py` (first half)

**Status:** not_started | **Effort:** L | **Depends on:** 052.3, 052.5
**Files:** `src/tapps_brain/mcp_server.py` lines 1 – ~1200 (2453 LOC total)
**Verification:** `pytest tests/unit/test_mcp*.py tests/integration/test_mcp*.py -v`

Focus areas:
- Tool registration: 64 tools as of 2026-03-29 (cross-check `docs/generated/mcp-tools-manifest.json`).
- Input schema validation; error responses are structured (`isError: true`).
- `CallToolResult` unwrapping (GitHub #46 regression guard).

---

### STORY-052.11: Interfaces — `mcp_server.py` (second half) + resources

**Status:** not_started | **Effort:** L | **Depends on:** 052.10
**Files:** `src/tapps_brain/mcp_server.py` lines ~1200 – end
**Verification:** `pytest tests/unit/test_mcp*.py tests/integration/test_mcp*.py -v`

Focus areas:
- MCP resources (8 URIs including `memory://agent-contract`, `memory://metrics`, `memory://stats`).
- Hive-aware MCP surface (EPIC-013 tools).
- `tapps_brain_relay_export`, `tapps_brain_session_end`, `tapps_brain_health` stability.

---

### STORY-052.12: Interfaces — `cli.py` (first half, commands)

**Status:** not_started | **Effort:** L | **Depends on:** 052.3, 052.5
**Files:** `src/tapps_brain/cli.py` lines 1 – ~1700 (3371 LOC total)
**Verification:** `pytest tests/unit/test_cli*.py tests/integration/test_cli*.py -v`

Focus areas:
- Typer command registration; `--json` flag consistency.
- Exit codes (0 success, 1 user error, 2 internal error).
- Flags documented in `--help` match docstrings.

---

### STORY-052.13: Interfaces — `cli.py` (second half) + IO helpers

**Status:** not_started | **Effort:** L | **Depends on:** 052.12
**Files:** `src/tapps_brain/cli.py` lines ~1700 – end, `io.py`, `markdown_import.py`, `markdown_sync.py`, `session_summary.py`, `session_index.py`, `visual_snapshot.py`
**Verification:** `pytest tests/unit/test_cli*.py tests/unit/test_markdown*.py tests/unit/test_session*.py -v`

Focus areas:
- `maintenance` subcommands (gc, stale, consolidation-merge-undo, consolidation-threshold-sweep, save-conflict-candidates, encrypt-db, rekey-db).
- `relay import` stdin + file paths.
- `visual_snapshot.py` help-key coverage.

---

### STORY-052.14: Config, profiles, onboarding

**Status:** not_started | **Effort:** M | **Depends on:** none
**Files:** `profile.py` (649), `profile_migrate.py`, `profiles/*.yaml`, `onboarding.py`, `feedback.py`
**Verification:** `pytest tests/unit/test_profile*.py tests/unit/test_onboarding.py tests/unit/test_feedback.py -v`

Focus areas:
- Profile YAML schema vs `MemoryProfile` Pydantic model parity.
- `seeding.seed_version` + `profile_seed_version` propagation.
- `hybrid_fusion` / `HybridFusionConfig` / `SafetyConfig` / `ConflictCheckConfig` / `limits.max_entries_per_group` coverage.
- Default profiles: `repo-brain.yaml`, and any others shipped.

---

### STORY-052.15: Observability — diagnostics, metrics, flywheel

**Status:** not_started | **Effort:** M | **Depends on:** none
**Files:** `diagnostics.py` (698), `health_check.py`, `metrics.py`, `otel_exporter.py`, `flywheel.py` (781), `evaluation.py` (850)
**Verification:** `pytest tests/unit/test_diagnostics*.py tests/unit/test_health*.py tests/unit/test_flywheel*.py tests/unit/test_evaluation*.py -v`

Focus areas:
- Health report field completeness vs documented contract.
- Metric name stability (EWMA, counters, `save_phase_summary`).
- `run_consolidation_threshold_sweep` + `run_save_conflict_candidate_report` determinism.

---

### STORY-052.16: Supporting — NLP, graph, crypto helpers

**Status:** not_started | **Effort:** S | **Depends on:** none
**Files:** `rake.py`, `textrank.py`, `pagerank.py`, `louvain.py`, `encryption_migrate.py`, `sqlcipher_util.py`
**Verification:** `pytest tests/unit/test_rake.py tests/unit/test_textrank*.py tests/unit/test_pagerank*.py tests/unit/test_louvain*.py tests/unit/test_encryption*.py tests/unit/test_sqlcipher*.py -v`

Focus areas:
- Graph algorithm correctness on degenerate inputs (empty graph, single node, cycles).
- `sqlcipher_util`: key handling, rekey path, no key material in logs.
- Encryption migrate: idempotency, rollback-on-failure.

---

### STORY-052.17: Package surface — `__init__.py`, re-exports, version

**Status:** not_started | **Effort:** S | **Depends on:** all preceding
**Files:** `src/tapps_brain/__init__.py`
**Verification:** `pytest tests/unit/test_public_api.py -v` (file test if it exists; else grep for `from tapps_brain import`)

Focus areas:
- Public API surface matches `README.md` promises.
- No accidental re-export of private `_*` symbols.
- `__version__` matches `pyproject.toml`.

---

### STORY-052.18: Final sweep — cross-module consistency + release gate

**Status:** not_started | **Effort:** M | **Depends on:** all preceding
**Files:** cross-module
**Verification:** `bash scripts/release-ready.sh`

Focus areas:
- Run `ruff check` + `ruff format --check` + `mypy --strict` + full `pytest` + `openclaw-plugin` tests.
- Review findings list across all stories: any duplicated patterns worth a shared helper? File a follow-up issue, do not refactor here.
- Update `STATUS.md`, `open-issues-roadmap.md`, and this epic's status to `done`.
- If any findings are deferred, ensure a GitHub issue exists for each.

#### Close-out

- [ ] All 17 review stories closed with findings notes.
- [ ] All must-fix bugs patched with tests.
- [ ] All deferred findings have GitHub issues.
- [ ] `release-ready.sh` green on `main`.

---

## Priority order

Execute stories in numeric order (052.1 → 052.18). Storage foundations land first so retrieval/lifecycle reviews can rely on a clean base. Interfaces (MCP/CLI) come after core logic. Supporting helpers and the final sweep close the epic.

## Tracking

- Link each story's PR back to this epic.
- Mirror story status in `.ralph/fix_plan.md` under a new `EPIC-052` section for Ralph-loop execution.
- Update `STATUS.md` epics table as stories complete.
