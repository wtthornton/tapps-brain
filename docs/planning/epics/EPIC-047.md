---
id: EPIC-047
title: "Quality loop, observability, and ops — research and upgrades"
status: done
priority: medium
created: 2026-03-31
tags: [feedback, diagnostics, flywheel, health, otel, metrics, integrity]
---

# EPIC-047: Quality loop, observability, and ops

## Context

Maps to **§6** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Relates to **EPIC-032** (OTel GenAI conventions) for overlap — cross-link in implementation.

## Success criteria

- [x] Operators can answer “is retrieval healthy?” without reading source (#63 extended if needed) — primary story: **047.4**.

## Stories

**§6 table order:** **047.1** feedback → **047.2** diagnostics → **047.3** flywheel → **047.4** health → **047.5** OTel → **047.6** rate limit → **047.7** integrity hash.

### STORY-047.1: User/agent feedback signals

**Status:** done | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/feedback.py`, `src/tapps_brain/models.py` (`FeedbackEvent` / store wiring), profile `FeedbackConfig`, `tests/unit/test_feedback.py`, `tests/unit/test_store_feedback.py`  
**Verification:** `pytest tests/unit/test_feedback.py tests/unit/test_store_feedback.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Implicit feedback** (clicks, dwell) common in search products — we have explicit events; map **recall_then_save** correction path (already partially in store).

#### Implementation themes

- [x] **Schema registry** for custom events in MCP with JSON Schema export.
- [x] **Privacy**: retention caps on feedback store.

---

### STORY-047.2: Diagnostics / SLO-style scorecard

**Status:** done | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/diagnostics.py`, `src/tapps_brain/mcp_server.py`, `src/tapps_brain/cli.py` (diagnostics commands), `tests/unit/test_diagnostics.py`, `tests/integration/test_diagnostics_integration.py`  
**Verification:** `pytest tests/unit/test_diagnostics.py tests/integration/test_diagnostics_integration.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **SLO error budgets** from EWMA — expose **burn rate** in report (math: budget consumption over window).

#### Implementation themes

- [x] **Dashboard** export (JSON) stable schema v1.
- [x] Link diagnostics **circuit state** to MCP tool errors (user-visible hint).

---

### STORY-047.3: Flywheel (Bayesian updates + optional LLM judge)

**Status:** done | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/flywheel.py`, `src/tapps_brain/_feature_flags.py` (optional LLM SDK probes), `tests/unit/test_flywheel.py`, `tests/integration/test_flywheel_integration.py`  
**Verification:** `pytest tests/unit/test_flywheel.py tests/integration/test_flywheel_integration.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **LLM-as-judge** cost/latency — keep **offline**; add **cache** of judgments keyed by content hash.
- **Thompson sampling** alternative for exploration of memory variants — research only.

#### Implementation themes

- [x] **Idempotent** judge runs (same input → same output recorded).
- [x] **Red-team** prompts for judge injection.

---

### STORY-047.4: Health checks (store + Hive + retrieval mode)

**Status:** done | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/health_check.py`, `src/tapps_brain/cli.py` (`diagnostics health`), `tests/unit/test_health_check.py`  
**Verification:** `pytest tests/unit/test_health_check.py -v --tb=short -m "not benchmark"`

#### Implementation themes

- [x] Add **latency percentiles** optional probe (micro-benchmark, not default hot path).
- [x] **Hive** connectivity vs **reachable file** distinction in report.

---

### STORY-047.5: Distributed tracing (OpenTelemetry)

**Status:** done | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `docs/guides/observability.md`, [`EPIC-032`](EPIC-032.md), `tests/unit/test_otel_exporter.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py -v --tb=short -m "not benchmark"` (optional manual span export checklist in observability guide)

#### Research notes (2026-forward)

- **GenAI semconv** for **RAG** spans (retrieve, embed, rerank) — align attribute names when spec stabilizes.

#### Implementation themes

- [x] Spans: **`memory.save`**, **`memory.recall`**, **`mcp.tool`** parent context propagation.
- [x] **Sampling** strategy documented (head-based default).

---

### STORY-047.6: Rate limiting (sliding window)

**Status:** done | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/rate_limiter.py`, `src/tapps_brain/store.py`, `tests/unit/test_rate_limiter.py`  
**Verification:** `pytest tests/unit/test_rate_limiter.py -v --tb=short -m "not benchmark"`

#### Implementation themes

- [x] **Per-agent** keys for MCP (not only global).
- [x] **429-style** error payload for MCP clients.

---

### STORY-047.7: Integrity (per-entry hash)

**Status:** done | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/integrity.py`, `src/tapps_brain/models.py` (`integrity_hash`), `tests/unit/test_integrity.py`, `tests/unit/test_verify_integrity.py`  
**Verification:** `pytest tests/unit/test_integrity.py tests/unit/test_verify_integrity.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Merkle tree** over store for tamper-evident snapshots — optional large story.

#### Implementation themes

- [x] CLI: **verify-integrity** sweep command.
- [x] Document **hash input canonicalization** (ordering of fields).

## Priority order

**047.4**, **047.6**, **047.7** (ops quick wins) → **047.1**, **047.2** → **047.5** (EPIC-032 alignment) → **047.3**.
