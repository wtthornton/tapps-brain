---
id: EPIC-051
title: "Cross-cutting architecture review — checklist-driven improvements"
status: planned
priority: medium
created: 2026-03-31
tags: [architecture, roadmap, retrieval, scale, security, observability]
---

# EPIC-051: Cross-cutting review (§10 checklist)

## Context

Maps to **§10** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) — meta items that span multiple subsystems. Individual spikes may spawn **GitHub issues** and link back here.

| §10 checklist item | Story |
|-------------------|--------|
| 1 Retrieval stack alternatives | **051.1** |
| 2 Freshness (lazy vs TTL jobs) | **051.2** |
| 3 Correctness / ontology / review queue | **051.3** |
| 4 Scale / service extraction | **051.4** |
| 5 Security / key mgmt / backup | **051.5** |
| 6 Save-path observability | **051.6** |

## Success criteria

- [ ] Each checklist item has **decision recorded** (do / defer / wontfix) with owner date.

## Stories

**Checklist alignment:** stories **051.1**–**051.6** map 1:1 to [`features-and-technologies.md`](../../engineering/features-and-technologies.md) §10 items (see table under Context).

### STORY-051.1: Retrieval stack alternatives (learned sparse, ColBERT, managed vector DB)

**Status:** planned | **Effort:** XL | **Depends on:** none  
**Context refs:** [`EPIC-042.md`](EPIC-042.md), `src/tapps_brain/retrieval.py`, `src/tapps_brain/fusion.py`, `docs/planning/` (decision / ADR target)  
**Verification:** design-only; recorded decision or ADR under `docs/planning/` (no pytest gate). Optional baseline: `pytest tests/unit/test_memory_retrieval.py tests/unit/test_memory_fusion.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **SPLADE / sparse neural** encoders vs **BM25** — latency/quality tradeoff on coding corpus.
- **ColBERT-style** late interaction — storage multiplier vs precision.
- **Managed** DB (pgvector, Milvus) — operational cost vs embedded SQLite story.

#### Implementation themes

- [ ] **ADR** (architecture decision record): stay embedded vs hybrid architecture.
- [ ] If spike: **adapter protocol** for `VectorIndex` behind `sqlite_vec_index.py`.

---

### STORY-051.2: Freshness model (lazy decay vs explicit TTL jobs)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/decay.py`, `src/tapps_brain/gc.py`, [`EPIC-042.md`](EPIC-042.md) (STORY-042.8), `tests/unit/test_memory_decay.py`, `tests/unit/test_memory_gc.py`  
**Verification:** `pytest tests/unit/test_memory_decay.py tests/unit/test_memory_gc.py -v --tb=short -m "not benchmark"` (attach design doc when TTL / job shape is decided)

#### Implementation themes

- [ ] **Scheduled** `maintenance decay-refresh` optional command (no-op if lazy kept).
- [ ] **Metrics**: count of entries crossing stale threshold per day.

---

### STORY-051.3: Correctness beyond heuristics (ontology, human review queue)

**Status:** planned | **Effort:** XL | **Depends on:** none  
**Context refs:** `src/tapps_brain/contradictions.py`, `src/tapps_brain/feedback.py`, `tests/unit/test_contradictions.py`, `tests/unit/test_feedback.py`  
**Verification:** `pytest tests/unit/test_contradictions.py tests/unit/test_feedback.py -v --tb=short -m "not benchmark"` (plus written product spec / MVP checklist before new MCP surfaces ship)

#### Research notes (2026-forward)

- **Human-in-the-loop** queues are common for enterprise KB; **MCP tool** to list “pending contradictions.”

#### Implementation themes

- [ ] MVP: **flag** memories needing review (`needs_review` tag) from conflict detector.
- [ ] MCP: **list** / **resolve** review items (deterministic state machine).

---

### STORY-051.4: Scale path (single-node limits → queue / service extraction)

**Status:** planned | **Effort:** XL | **Depends on:** STORY-050.2  
**Context refs:** `docs/engineering/system-architecture.md`, `docs/planning/open-issues-roadmap.md` (MemoryStore modularization backlog), [`EPIC-042-feature-tech-index.md`](EPIC-042-feature-tech-index.md), `tests/unit/test_concurrent.py`, `tests/unit/test_memory_foundation_integration.py`  
**Verification:** `pytest tests/unit/test_concurrent.py tests/unit/test_memory_foundation_integration.py -v --tb=short -m "not benchmark"` (add capacity doc + optional benchmark harness when publishing QPS claims)

#### Implementation themes

- [ ] Publish **supported QPS** envelope for MCP **read** vs **write**.
- [ ] **Service extraction** ADR: which boundaries (read replica? write API?).

---

### STORY-051.5: Security operations (SQLCipher key management and backup)

**Status:** planned | **Effort:** L | **Depends on:** none  
**Context refs:** `docs/guides/sqlcipher.md`, [`EPIC-043.md`](EPIC-043.md) (STORY-043.6), `tests/unit/test_sqlcipher_util.py`  
**Verification:** `pytest -m "requires_encryption and not benchmark" -v --tb=short` (where native SQLCipher available); runbook review checklist in `docs/guides/sqlcipher.md`

#### Implementation themes

- [ ] **Envelope encryption** pattern doc (wrap DEK with KMS).
- [ ] **Backup**: copy `.db` + **verify** restore procedure quarterly checklist.

---

### STORY-051.6: Save-path observability (consolidation / conflict / embed latency)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `docs/planning/open-issues-roadmap.md` (save-path observability backlog), `src/tapps_brain/metrics.py`, `src/tapps_brain/store.py`, `tests/unit/test_metrics.py`, `tests/unit/test_health_check.py`  
**Verification:** `pytest tests/unit/test_metrics.py tests/unit/test_health_check.py -v --tb=short -m "not benchmark"` (extend when new save-phase metrics land)

#### Research notes (2026-forward)

- **RED metrics** methodology for **histograms** (p50/p95) on `save_ms` sub-phases.

#### Implementation themes

- [ ] Break down **MetricsTimer** sub-spans: safety, persist, hive, relations, consolidate, embed.
- [ ] Surface **top counters** in `store.get_metrics()` and MCP health JSON.

## Priority order

**Trace order (matches [`features-and-technologies.md`](../../engineering/features-and-technologies.md) §10 checklist 1–6):** **051.1** → **051.2** → **051.3** → **051.4** → **051.5** → **051.6**.

**Suggested execution order (value vs effort):** **051.6** and **051.2** (observability + freshness — smaller, de-risks ops) → **051.5** (security runbook) → **051.1**, **051.3**, **051.4** (architecture spikes: retrieval stack, correctness queue, scale).
