---
id: EPIC-044
title: "Ingestion, deduplication, and lifecycle — research and upgrades"
status: planned
priority: high
created: 2026-03-31
tags: [safety, dedup, contradictions, consolidation, gc, seeding, caps]
---

# EPIC-044: Ingestion, deduplication, and lifecycle

## Context

Maps to **§3** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md).

## Success criteria

- [ ] Save path **determinism** preserved for default profile (no silent LLM calls).
- [ ] Any new heuristics behind **flags** with tests and docs.

## Stories

**§3 table order:** **044.1** RAG safety → **044.2** Bloom dedup → **044.3** conflicts → **044.4** consolidation → **044.5** GC → **044.6** seeding → **044.7** caps/eviction.

### STORY-044.1: Write-time content safety (RAG safety)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/safety.py`, `src/tapps_brain/store.py`, `tests/unit/test_safety.py`  
**Verification:** `pytest tests/unit/test_safety.py -v --tb=short -m "not benchmark"`

#### Code baseline

Rule-based checks in `safety.py` on save and before injection; integrated on `MemoryStore.save` path.

#### Research notes (2026-forward)

- **Defense in depth:** pattern lists age quickly; consider **allowlist** modes for trusted agents.
- **Unicode homoglyph** and **markdown/HTML** injection in stored values — expand normalizer tests.

#### Implementation themes

- [ ] Versioned **ruleset** with semver in profile or config.
- [ ] Metrics: **block** vs **sanitize** counts.

---

### STORY-044.2: Near-duplicate detection (Bloom + normalize)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/bloom.py`, `src/tapps_brain/store.py` (dedup / reinforce path), `tests/unit/test_bloom.py`  
**Verification:** `pytest tests/unit/test_bloom.py -v --tb=short -m "not benchmark"`

#### Code baseline

Bloom filter + `normalize_for_dedup` fast path; may reinforce existing key instead of inserting.

#### Research notes (2026-forward)

- **Bloom false positives** → unnecessary reinforce path; false negatives → dup rows — tune **bits** and **hash** count vs memory.
- **SimHash/MinHash** for fuzzy dup at higher cost — optional second stage.

#### Implementation themes

- [ ] Expose **expected error rate** in doc given default size.
- [ ] Spike: **normalize** Unicode NFKC for dup compare.

---

### STORY-044.3: Contradiction / conflict handling

**Status:** planned | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/contradictions.py`, `src/tapps_brain/store.py` (`conflict_check`), GitHub #44, `tests/unit/test_contradictions.py`, `tests/unit/test_contradictions_detect.py`  
**Verification:** `pytest tests/unit/test_contradictions.py tests/unit/test_contradictions_detect.py -v --tb=short -m "not benchmark"`

#### Code baseline

`detect_save_conflicts` optional on save; temporal invalidation of conflicting entries when enabled.

#### Research notes (2026-forward)

- Pairwise **NLI-style** models could label entail/contradict — **offline** or **async** only to keep sync path fast.
- **Temporal logic:** ensure **invalid_at** / **valid_at** ordering invariants under concurrency (see concurrent save tests).

#### Implementation themes

- [x] **exclude_key:** the key being saved is not treated as a separate conflicting row (`detect_save_conflicts(..., exclude_key=key)`); prevents concurrent same-key updates from tripping ``valid_at``/``invalid_at`` ordering (2026-04-02).
- [ ] User-visible **reason** on conflict (`contradiction_reason` population audit).
- [ ] Profile: **aggressiveness** tiers for `detect_save_conflicts`.

---

### STORY-044.4: Deterministic merge / consolidation

**Status:** planned | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/consolidation.py`, `src/tapps_brain/similarity.py`, `src/tapps_brain/auto_consolidation.py`, `src/tapps_brain/store.py`, `tests/unit/test_memory_consolidation.py`, `tests/unit/test_memory_auto_consolidation.py`, `tests/unit/test_consolidation_config.py`  
**Verification:** `pytest tests/unit/test_memory_consolidation.py tests/unit/test_memory_auto_consolidation.py tests/unit/test_consolidation_config.py -v --tb=short -m "not benchmark"`

#### Code baseline

Deterministic merge (Jaccard / TF-IDF / topic); auto path on save when `ConsolidationConfig.enabled`.

#### Research notes (2026-forward)

- **Jaccard + TF-IDF** are classic; **BERTScore**-style similarity requires models — out of core unless optional.
- **Minimum description length** merges — research criterion for “one summary vs many” without LLM.

#### Implementation themes

- [ ] **Undo** or **audit** trail for auto-consolidated keys (operator trust).
- [ ] Threshold **sensitivity** sweep in `evaluation.py` or benchmark.

---

### STORY-044.5: Garbage collection / archival

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/gc.py`, `src/tapps_brain/profile.py` (`GCConfig`), `src/tapps_brain/cli.py` / `src/tapps_brain/mcp_server.py` (maintenance), `tests/unit/test_memory_gc.py`, `tests/unit/test_gc_config.py`  
**Verification:** `pytest tests/unit/test_memory_gc.py tests/unit/test_gc_config.py -v --tb=short -m "not benchmark"`

#### Code baseline

Tier-aware archival via `MemoryGarbageCollector`; profile-driven thresholds.

#### Research notes (2026-forward)

- **Tier-aware** decay already interacts with GC — document **interaction** with consolidation-invalidated rows.
- Optional **time-based** policies (TTL) as explicit jobs vs lazy decay.

#### Implementation themes

- [ ] **Dry-run** GC report: what would archive with counts.
- [ ] Metrics: **archived** bytes / rows per run.

---

### STORY-044.6: Profile-driven seeding

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/seeding.py`, `tests/unit/test_seeding.py`  
**Verification:** `pytest tests/unit/test_seeding.py -v --tb=short -m "not benchmark"`

#### Code baseline

`seed_from_profile` on empty store; `reseed_from_profile` touches `auto-seeded` entries only.

#### Research notes (2026-forward)

- **Machine-readable** project signals (SBOM, package.json) could enrich seeds — deterministic extractors only.

#### Implementation themes

- [ ] Optional seed **version** in profile for **reseed** diff.
- [ ] Spike: conflict_check **off** for seeds documented as intentional (already in tests).

---

### STORY-044.7: Caps and eviction

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/store.py` (max entries / eviction), `src/tapps_brain/profile.py` (`limits.max_entries`), `tests/unit/test_memory_store.py`  
**Verification:** `pytest tests/unit/test_memory_store.py -k evict -v --tb=short -m "not benchmark"`

#### Code baseline

Default cap 5000; lowest-confidence eviction when over `limits.max_entries`.

#### Research notes (2026-forward)

- **W-TinyLFU** or **LRU** alternatives to “lowest confidence” — may better match **recency** importance.
- **Fairness** across `memory_group` — avoid one group evicting another’s global budget if groups are added.

#### Implementation themes

- [ ] Document **eviction policy** formally in engineering doc.
- [ ] Optional: **per-group caps** in profile.

## Priority order

**044.1** (safety) and **044.2** (dedup) first — limit bad or duplicate data entering the store. Then **044.3** (conflicts), **044.4** (consolidation), **044.5** (GC), **044.7** (caps), **044.6** (seeding — first-run UX).
