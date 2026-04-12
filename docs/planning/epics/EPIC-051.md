---
id: EPIC-051
title: "Cross-cutting architecture review — checklist-driven improvements"
status: done
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

- [x] Each checklist item has **decision recorded** (do / defer / wontfix) with owner date — **complete 2026-04-03** (ADRs **001**–**006** under [`adr/`](../adr/)).
  - **10.1 Retrieval stack:** done — [`ADR-001`](../adr/ADR-001-retrieval-stack.md); owner @wtthornton; **2026-04-03**.
  - **10.2 Freshness (lazy vs TTL):** done — [`ADR-002`](../adr/ADR-002-freshness-lazy-decay-vs-ttl.md); owner @wtthornton; **2026-04-03**.
  - **10.3 Correctness / review queue:** done — [`ADR-003`](../adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md); owner @wtthornton; **2026-04-03**.
  - **10.4 Scale / service extraction:** done — [`ADR-004`](../adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md); owner @wtthornton; **2026-04-03**.
  - **10.5 Security (SQLCipher ops):** done — [`ADR-005`](../adr/ADR-005-sqlcipher-key-backup-operations.md); owner @wtthornton; **2026-04-03**; runbook `sqlcipher.md` *(guide removed — SQLite retired in ADR-007)*.
  - **10.6 Save-path observability:** done — [`ADR-006`](../adr/ADR-006-save-path-observability.md); owner @wtthornton; **2026-04-03** (histograms + `save_phase_summary` shipped; deeper metrics per trigger **(a)**).

## Stories

**Checklist alignment:** stories **051.1**–**051.6** map 1:1 to [`features-and-technologies.md`](../../engineering/features-and-technologies.md) §10 items (see table under Context).

### STORY-051.1: Retrieval stack alternatives (learned sparse, ColBERT, managed vector DB)

**Status:** done | **Effort:** XL | **Depends on:** none  
**Owner / closed:** @wtthornton — **2026-04-03**  
**Context refs:** [`EPIC-042.md`](EPIC-042.md), `src/tapps_brain/retrieval.py`, `src/tapps_brain/fusion.py`, [`ADR-001`](../adr/ADR-001-retrieval-stack.md)  
**Verification:** design-only; recorded decision or ADR under `docs/planning/` (no pytest gate). Optional baseline: `pytest tests/unit/test_memory_retrieval.py tests/unit/test_memory_fusion.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **SPLADE / sparse neural** encoders vs **BM25** — latency/quality tradeoff on coding corpus.
- **ColBERT-style** late interaction — storage multiplier vs precision.
- **Managed** DB (pgvector, Milvus) — operational cost vs embedded SQLite story.

#### Implementation themes

- [x] **ADR** (architecture decision record): stay embedded SQLite–first; defer learned sparse, ColBERT, managed vector DB for shipped core — [`ADR-001`](../adr/ADR-001-retrieval-stack.md).
- [ ] **Deferred:** **adapter protocol** for `VectorIndex` behind `sqlite_vec_index.py` — not required for this story; reopen with a new ADR if external index becomes a product goal.

---

### STORY-051.2: Freshness model (lazy decay vs explicit TTL jobs)

**Status:** done | **Effort:** M | **Depends on:** none  
**Owner / closed:** @wtthornton — **2026-04-03**  
**Context refs:** `src/tapps_brain/decay.py`, `src/tapps_brain/gc.py`, [`EPIC-042.md`](EPIC-042.md) (STORY-042.8), [`ADR-002`](../adr/ADR-002-freshness-lazy-decay-vs-ttl.md), `tests/unit/test_memory_decay.py`, `tests/unit/test_memory_gc.py`  
**Verification:** [`ADR-002`](../adr/ADR-002-freshness-lazy-decay-vs-ttl.md) (design gate). Baseline regression: `pytest tests/unit/test_memory_decay.py tests/unit/test_memory_gc.py -v --tb=short -m "not benchmark"` (green on **2026-04-03**).

#### Implementation themes

- [ ] **Deferred:** **Scheduled** `maintenance decay-refresh` — not required while lazy decay + GC remain canonical; see [`ADR-002`](../adr/ADR-002-freshness-lazy-decay-vs-ttl.md).
- [ ] **Deferred:** **Metrics** — count of entries crossing stale threshold per day; revisit with lifecycle observability work if needed.


---

### STORY-051.3: Correctness beyond heuristics (ontology, human review queue)

**Status:** done | **Effort:** XL | **Depends on:** none  
**Owner / closed:** @wtthornton — **2026-04-03**  
**Context refs:** `src/tapps_brain/contradictions.py`, `src/tapps_brain/feedback.py`, [`ADR-003`](../adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md), `tests/unit/test_contradictions.py`, `tests/unit/test_feedback.py`  
**Verification:** [`ADR-003`](../adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md) (design gate). Baseline regression: `pytest tests/unit/test_contradictions.py tests/unit/test_feedback.py -v --tb=short -m "not benchmark"` (green on **2026-04-03**). **New MCP / queue surfaces** still require a separate product spec + [`PLANNING.md`](../PLANNING.md) trigger **(c)** before implementation.

#### Research notes (2026-forward)

- **Human-in-the-loop** queues are common for enterprise KB; **MCP tool** to list “pending contradictions.”

#### Implementation themes

- [ ] **Deferred:** MVP **`needs_review`** tagging from conflict detector — not in core scope; see [`ADR-003`](../adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md).
- [ ] **Deferred:** MCP **list** / **resolve** review queue — requires explicit product requirement (trigger **(c)**) + spec; still **no** LLM on sync `save`.

---

### STORY-051.4: Scale path (single-node limits → queue / service extraction)

**Status:** done | **Effort:** XL | **Depends on:** STORY-050.2 (**satisfied** — EPIC-050 **050.2** **done** 2026-04-02)  
**Owner / closed:** @wtthornton — **2026-04-03**  
**Context refs:** [`ADR-004`](../adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md), `docs/engineering/system-architecture.md`, `docs/planning/open-issues-roadmap.md` (row 22), [`EPIC-042-feature-tech-index.md`](EPIC-042-feature-tech-index.md), `tests/unit/test_concurrent.py`, `tests/unit/test_memory_foundation_integration.py`  
**Verification:** [`ADR-004`](../adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md) (design gate). Baseline regression: `pytest tests/unit/test_concurrent.py tests/unit/test_memory_foundation_integration.py -v --tb=short -m "not benchmark"` (green on **2026-04-03**).

#### Implementation themes

- [ ] **Deferred:** Publish **supported QPS** envelope — needs benchmark harness + environment profile; see [`ADR-004`](../adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md).
- [x] **Service extraction** boundary — **recorded** in [`ADR-004`](../adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md) (defer mandatory extraction; single-node posture maintained).

---

### STORY-051.5: Security operations (SQLCipher key management and backup)

**Status:** done | **Effort:** L | **Depends on:** none  
**Owner / closed:** @wtthornton — **2026-04-03**  
**Context refs:** [`ADR-005`](../adr/ADR-005-sqlcipher-key-backup-operations.md), `sqlcipher.md` *(guide removed — SQLite retired in ADR-007)*, [`EPIC-043.md`](EPIC-043.md) (STORY-043.6), `tests/unit/test_sqlcipher_util.py`, `tests/unit/test_encryption_migrate.py`  
**Verification:** [`ADR-005`](../adr/ADR-005-sqlcipher-key-backup-operations.md) + expanded `sqlcipher.md` *(guide removed — SQLite retired in ADR-007)*. `pytest -m "requires_encryption and not benchmark" -v --tb=short` when native SQLCipher available (else skipped in CI). Baseline without cipher round-trips: `pytest tests/unit/test_sqlcipher_util.py tests/unit/test_encryption_migrate.py -v --tb=short -m "not benchmark and not requires_encryption"` (green on **2026-04-03**).

#### Implementation themes

- [ ] **Deferred:** Vendor-specific **envelope encryption / KMS** how-to in-repo — host-owned; see [`ADR-005`](../adr/ADR-005-sqlcipher-key-backup-operations.md) *(sqlcipher.md guide removed — SQLite retired in ADR-007)*.
- [x] **Backup / verify** — **lost passphrase** warning + **backup and restore verification** checklist + optional re-key drill in `sqlcipher.md` *(guide removed — SQLite retired in ADR-007)*.

---

### STORY-051.6: Save-path observability (consolidation / conflict / embed latency)

**Status:** done | **Effort:** M | **Depends on:** none  
**Owner / closed:** @wtthornton — **2026-04-03**  
**Context refs:** [`ADR-006`](../adr/ADR-006-save-path-observability.md), `docs/planning/open-issues-roadmap.md`, `src/tapps_brain/metrics.py`, `src/tapps_brain/store.py`, `tests/unit/test_metrics.py`, `tests/unit/test_health_check.py`  
**Verification:** [`ADR-006`](../adr/ADR-006-save-path-observability.md) + `pytest tests/unit/test_metrics.py tests/unit/test_health_check.py -v --tb=short -m "not benchmark"` (green on **2026-04-03**).

#### Research notes (2026-forward)

- **RED metrics** methodology for **histograms** (p50/p95) on `save_ms` sub-phases.

#### Implementation themes

- [x] Break down **MetricsTimer** sub-spans on save: lock/build, persist, hive, relations, consolidate, embed (`store.save.phase.*` histograms; 2026-04-02).
- [x] Surface in `store.get_metrics()` and MCP **`memory://metrics`** resource (full snapshot includes new histograms).
- [x] **`save_phase_summary`** on live store **health** / native health (roadmap row 20).
- [ ] **Deferred:** Richer compact save-phase lines on text **`diagnostics health`** / extra **`HealthReport`** fields — optional UX; see [`ADR-006`](../adr/ADR-006-save-path-observability.md).
- [ ] **Deferred:** Deeper save-path / consolidation correlation metrics — trigger **(a)** in [`PLANNING.md`](../PLANNING.md) *Optional backlog gating*.

## Priority order

**Trace order (matches [`features-and-technologies.md`](../../engineering/features-and-technologies.md) §10 checklist 1–6):** **051.1** → **051.2** → **051.3** → **051.4** → **051.5** → **051.6**.

**Suggested execution order (value vs effort):** **051.6** and **051.2** (observability + freshness — smaller, de-risks ops) → **051.5** (security runbook) → **051.1**, **051.3**, **051.4** (architecture spikes: retrieval stack, correctness queue, scale).
