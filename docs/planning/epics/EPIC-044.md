---
id: EPIC-044
title: "Ingestion, deduplication, and lifecycle — research and upgrades"
status: in_progress
priority: high
created: 2026-03-31
tags: [safety, dedup, contradictions, consolidation, gc, seeding, caps]
---

# EPIC-044: Ingestion, deduplication, and lifecycle

## Context

Maps to **§3** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md).

## Success criteria

- [x] Save path **determinism** preserved for default profile (no silent LLM calls). *(Optional NLI/async conflict research in STORY-044.3 notes stays out of core.)*
- [x] New heuristics behind **flags** / profile fields with tests and docs *(per stories, including per-group caps).*

## Stories

**§3 table order:** **044.1** RAG safety → **044.2** Bloom dedup → **044.3** conflicts → **044.4** consolidation → **044.5** GC → **044.6** seeding → **044.7** caps/eviction.

### STORY-044.1: Write-time content safety (RAG safety)

**Status:** done (2026-04-02) | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/safety.py`, `src/tapps_brain/store.py`, `tests/unit/test_safety.py`  
**Verification:** `pytest tests/unit/test_safety.py -v --tb=short -m "not benchmark"`

#### Code baseline

Rule-based checks in `safety.py` on save and before injection; integrated on `MemoryStore.save` path.

#### Research notes (2026-forward)

- **Defense in depth:** pattern lists age quickly; consider **allowlist** modes for trusted agents.
- **Unicode homoglyph** and **markdown/HTML** injection in stored values — expand normalizer tests.

#### Implementation themes

- [x] Versioned **ruleset** with semver in profile (`profile.safety.ruleset_version` → `SafetyConfig`; `DEFAULT_SAFETY_RULESET_VERSION` / `resolve_safety_ruleset_version`; unknown pins log `rag_safety_unknown_ruleset_version` and fall back) (2026-04-02).
- [x] Metrics: **block** vs **sanitize** — counters `rag_safety.blocked` / `rag_safety.sanitized` on optional `MetricsCollector`; `StoreHealthReport` exposes `rag_safety_ruleset_version`, `rag_safety_blocked_count`, `rag_safety_sanitized_count` (2026-04-02).
- [x] Save path aligned with `safety.py`: any `safe=False` blocks save (removed redundant `_RAG_BLOCK_THRESHOLD` gate); sanitize when `sanitised_content` is set (2026-04-02).
- [x] Injection uses profile ruleset + store metrics; injects **sanitised** text when the sanitize path applies (2026-04-02).

---

### STORY-044.2: Near-duplicate detection (Bloom + normalize)

**Status:** done (2026-04-02) | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/bloom.py`, `src/tapps_brain/store.py` (dedup / reinforce path), `tests/unit/test_bloom.py`  
**Verification:** `pytest tests/unit/test_bloom.py -v --tb=short -m "not benchmark"`

#### Code baseline

Bloom filter + `normalize_for_dedup` fast path; may reinforce existing key instead of inserting.

#### Research notes (2026-forward)

- **Bloom false positives** → unnecessary reinforce path; false negatives → dup rows — tune **bits** and **hash** count vs memory.
- **SimHash/MinHash** for fuzzy dup at higher cost — optional second stage.

#### Implementation themes

- [x] Expose **expected false-positive rate** — module + class docstrings; ``bloom_false_positive_probability`` and ``BloomFilter.approximate_false_positive_rate``; properties ``bit_size`` / ``hash_count`` (2026-04-02).
- [x] **NFKC** in ``normalize_for_dedup`` before lowercase / whitespace collapse (2026-04-02).

---

### STORY-044.3: Contradiction / conflict handling

**Status:** in_progress (core save-path shipped 2026-04-02; NLI/async research remains backlog) | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/contradictions.py`, `src/tapps_brain/store.py` (`conflict_check`), GitHub #44, `tests/unit/test_contradictions.py`, `tests/unit/test_contradictions_detect.py`  
**Verification:** `pytest tests/unit/test_contradictions.py tests/unit/test_contradictions_detect.py -v --tb=short -m "not benchmark"`

#### Code baseline

`detect_save_conflicts` optional on save; temporal invalidation of conflicting entries when enabled.

#### Research notes (2026-forward)

- Pairwise **NLI-style** models could label entail/contradict — **offline** or **async** only to keep sync path fast.
- **Temporal logic:** ensure **invalid_at** / **valid_at** ordering invariants under concurrency (see concurrent save tests).

#### Implementation themes

- [x] **exclude_key:** the key being saved is not treated as a separate conflicting row (`detect_save_conflicts(..., exclude_key=key)`); prevents concurrent same-key updates from tripping ``valid_at``/``invalid_at`` ordering (2026-04-02).
- [x] User-visible **reason** on conflict: invalidated rows get ``contradicted=True`` and deterministic ``contradiction_reason`` (plus structured log ``conflicts`` with per-key similarity); ``detect_save_conflicts`` returns ``SaveConflictHit`` (entry + score) (2026-04-02).
- [x] Profile: **aggressiveness** tiers via ``MemoryProfile.conflict_check`` (`ConflictCheckConfig`: ``low`` / ``medium`` / ``high`` → thresholds 0.75 / 0.6 / 0.45, or explicit ``similarity_threshold``); wired in ``MemoryStore.save`` (2026-04-02).

---

### STORY-044.4: Deterministic merge / consolidation

**Status:** done (2026-04-03) — audit, threshold sweep, merge undo | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/consolidation.py`, `src/tapps_brain/similarity.py`, `src/tapps_brain/auto_consolidation.py`, `src/tapps_brain/store.py`, `tests/unit/test_memory_consolidation.py`, `tests/unit/test_memory_auto_consolidation.py`, `tests/unit/test_consolidation_config.py`  
**Verification:** `pytest tests/unit/test_memory_consolidation.py tests/unit/test_memory_auto_consolidation.py tests/unit/test_consolidation_config.py -v --tb=short -m "not benchmark"` (includes `TestConsolidationMergeUndo` for merge undo).

#### Code baseline

Deterministic merge (Jaccard / TF-IDF / topic); auto path on save when `ConsolidationConfig.enabled`.

#### Research notes (2026-forward)

- **Jaccard + TF-IDF** are classic; **BERTScore**-style similarity requires models — out of core unless optional.
- **Minimum description length** merges — research criterion for “one summary vs many” without LLM.

#### Implementation themes

- [x] **Audit** trail for auto-consolidation — JSONL ``memory_log.jsonl`` actions ``consolidation_merge`` (key = merged entry; ``source_keys``, ``trigger`` ``save``/``periodic_scan``, ``threshold``, ``consolidation_reason``) and ``consolidation_source`` per superseded key (``superseded_by``, ``trigger``, ``threshold``) (2026-04-02). Query via ``tapps-brain memory audit --type …`` / ``MemoryStore.audit``.
- [x] **Undo** (revert merge) — ``MemoryStore.undo_consolidation_merge`` / ``undo_consolidation_merge`` uses the last ``consolidation_merge`` audit row; restores sources (clears ``contradicted`` / temporal supersede fields), deletes the consolidated row, ``delete_relations`` for that key, appends ``consolidation_merge_undo``; CLI ``maintenance consolidation-merge-undo`` (2026-04-03). Consolidated row save uses ``skip_consolidation=True`` to avoid recursive merge-on-save.
- [x] Threshold **sensitivity** sweep — ``evaluation.run_consolidation_threshold_sweep`` + report models (deterministic; no store mutations) (2026-04-02).
- [x] CLI **read-only sweep** — ``tapps-brain maintenance consolidation-threshold-sweep`` (JSON + table; optional ``--thresholds``, ``--min-group-size``, ``--include-contradicted``) (2026-04-02).

---

### STORY-044.5: Garbage collection / archival

**Status:** done (2026-04-02) | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/gc.py`, `src/tapps_brain/profile.py` (`GCConfig`), `src/tapps_brain/cli.py` / `src/tapps_brain/mcp_server.py` (maintenance), `tests/unit/test_memory_gc.py`, `tests/unit/test_gc_config.py`  
**Verification:** `pytest tests/unit/test_memory_gc.py tests/unit/test_gc_config.py -v --tb=short -m "not benchmark"`

#### Code baseline

Tier-aware archival via `MemoryGarbageCollector`; profile-driven thresholds.

#### Research notes (2026-forward)

- **Tier-aware** decay already interacts with GC — document **interaction** with consolidation-invalidated rows.
- Optional **time-based** policies (TTL) as explicit jobs vs lazy decay.

#### Implementation themes

- [x] **Dry-run** GC report: ``GCResult`` includes ``reason_counts`` (per reason code), ``estimated_archive_bytes``, and candidate keys; CLI/MCP delegate to ``MemoryStore.gc`` (2026-04-02).
- [x] Metrics: counters ``store.gc.archived`` (rows) and ``store.gc.archive_bytes`` (UTF-8 appended per live run); ``StoreHealthReport`` exposes ``gc_runs_total``, ``gc_archived_rows_total``, ``gc_archive_bytes_total`` (2026-04-02).
- [x] Canonical archive path aligned to ``{store_dir}/archive.jsonl`` (was ``gc_archive.jsonl`` on ``MemoryStore.gc`` only) (2026-04-02).

---

### STORY-044.6: Profile-driven seeding

**Status:** done (2026-04-02) | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/seeding.py`, `tests/unit/test_seeding.py`  
**Verification:** `pytest tests/unit/test_seeding.py -v --tb=short -m "not benchmark"`

#### Code baseline

`seed_from_profile` on empty store; `reseed_from_profile` touches `auto-seeded` entries only.

#### Research notes (2026-forward)

- **Machine-readable** project signals (SBOM, package.json) could enrich seeds — deterministic extractors only.

#### Implementation themes

- [x] Optional seed **version** — ``MemoryProfile.seeding.seed_version``; summaries include ``profile_seed_version`` (2026-04-02).
- [x] Operator **visibility** — ``StoreHealthReport.profile_seed_version``, ``maintenance health``, native ``run_health_check`` ``store.profile_seed_version``, ``memory://stats`` (2026-04-02).
- [x] ``conflict_check`` on seed saves documented in ``seeding`` module docstring (2026-04-02).

---

### STORY-044.7: Caps and eviction

**Status:** done (2026-04-02; per-group caps 2026-04-03) | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/store.py` (max entries / eviction), `src/tapps_brain/profile.py` (`limits.max_entries`, `limits.max_entries_per_group`), `tests/unit/test_memory_store.py`  
**Verification:** `pytest tests/unit/test_memory_store.py::TestMemoryStoreEviction tests/unit/test_memory_store.py::TestMemoryStorePerGroupEviction -v --tb=short -m "not benchmark"`

#### Code baseline

Default cap 5000; lowest-confidence eviction when over `limits.max_entries`.

#### Research notes (2026-forward)

- **W-TinyLFU** or **LRU** alternatives to “lowest confidence” — may better match **recency** importance.
- **Fairness** across `memory_group` — avoid one group evicting another’s global budget if groups are added.

#### Implementation themes

- [x] Document **eviction policy** in [`data-stores-and-schema.md`](../../engineering/data-stores-and-schema.md) (cross-linked from [`features-and-technologies.md`](../../engineering/features-and-technologies.md) + [`profiles.md`](../../guides/profiles.md)) (2026-04-02).
- [x] Optional **per-group caps** — `MemoryProfile.limits.max_entries_per_group`; eviction within bucket; global overflow prefers incoming `memory_group` when per-group mode is enabled; health / MCP `memory://stats` / native health / CLI stats (2026-04-03).

## Priority order

**044.1** (safety) and **044.2** (dedup) first — limit bad or duplicate data entering the store. Then **044.3** (conflicts), **044.4** (consolidation), **044.5** (GC), **044.7** (caps), **044.6** (seeding — first-run UX).
