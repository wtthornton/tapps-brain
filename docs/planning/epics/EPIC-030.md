---
id: EPIC-030
title: "Diagnostics & self-monitoring — quality scorecard and anomaly detection"
status: done
completed: 2026-03-23
priority: high
created: 2026-03-23
tags: [diagnostics, self-monitoring, quality, anomaly-detection]
---

# EPIC-030: Diagnostics & Self-Monitoring — Quality Scorecard and Anomaly Detection

## Context

EPIC-007 gave tapps-brain operational observability: metrics (counters, histograms), audit trail, and health checks. But `health()` answers "what is the store's state?" — not "is the store performing well?" There is no quality assessment, no anomaly detection, and no way for the system to flag its own degradation.

The Cloud Security Alliance's Cognitive Degradation Resilience (CDR) framework (2025) defines a 6-stage degradation lifecycle for AI systems and recommends health probes, entropy drift detection, and memory quarantine as core runtime controls. 83% of successful self-healing implementations use tiered autonomy: auto-remediate routine issues, flag anomalies for review, and escalate architectural changes to humans.

Research on pluggable health frameworks (Spring Boot Actuator, Great Expectations, Kubernetes probes) shows that the most successful monitoring systems use **Protocol-based extensibility** — each quality dimension is an independent check that can be registered, weighted, and composed. This aligns with tapps-brain's existing `_protocols.py` pattern.

This epic adds a diagnostics layer that continuously assesses retrieval quality, data health, and operational anomalies. It computes a composite quality scorecard (no LLM required — fully deterministic), detects anomalies via EWMA drift detection, and exposes a 4-state circuit breaker that degrades gracefully when quality drops. It builds on EPIC-007's metrics infrastructure and is designed to consume EPIC-029's feedback signals when available (but does not require EPIC-029).

**Multi-project design**: Each `MemoryStore` instance computes its own diagnostics (project-scoped). Host projects can register custom quality dimensions (STORY-030.8) with their own scoring functions. When Hive is enabled, per-namespace health is tracked independently (STORY-030.9).

## Success Criteria

- [x] `store.diagnostics()` returns a `DiagnosticsReport` with composite quality score and per-dimension breakdowns
- [x] Six built-in quality dimensions scored: retrieval effectiveness, freshness, completeness, duplication, staleness, integrity
- [x] Host projects can register custom quality dimensions via `HealthDimension` Protocol
- [x] Anomaly detection via EWMA drift detection flags quality degradation with configurable thresholds
- [x] 4-state circuit breaker (CLOSED/DEGRADED/OPEN/HALF_OPEN) degrades gracefully when quality drops (`RecallResult.quality_warning` when not CLOSED)
- [x] Quality history tracked over time (`diagnostics_history` table, `store.diagnostics_history()`)
- [x] Per-namespace Hive diagnostics when Hive is enabled
- [x] `memory://diagnostics` MCP resource; `diagnostics_report` and `diagnostics_history` MCP tools
- [x] CLI: `tapps-brain diagnostics report|history` with json/table output
- [x] Zero new external dependencies
- [x] Overall test coverage stays at 95%+

## Stories

### STORY-030.1: Quality dimensions and scoring model

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/metrics.py`, `src/tapps_brain/models.py`, `src/tapps_brain/store.py`, `src/tapps_brain/_protocols.py`
**Verification:** `pytest tests/unit/test_diagnostics.py::TestQualityScoring -v`

#### Why

The quality scorecard is the core abstraction. Each dimension measures a distinct aspect of store health, and the composite score provides a single "is this store healthy?" answer. All computations are deterministic — no LLM, no external calls. Following the Spring Boot Actuator pattern, dimensions are Protocol-based and independently registerable, enabling host projects to add custom dimensions (STORY-030.8).

#### Acceptance Criteria

- [ ] New `src/tapps_brain/diagnostics.py` module
- [ ] `HealthDimension` Protocol in `_protocols.py`: `name: str`, `check(store) -> DimensionScore`, `default_weight: float`
- [ ] `DimensionScore` model: `dimension` (str name), `score` (float 0.0-1.0), `grade` (A/B/C/D/F mapped from score), `detail` (str explanation), `flags` (list of anomaly strings)
- [ ] All dimensions use **goalpost normalization** to the uniform 0.0-1.0 scale: each dimension defines its own min (0.0) and max (1.0) achievable values with domain-specific logic mapping raw metrics into that range
- [ ] Six built-in dimension implementations:
  - **Retrieval effectiveness**: Based on hit rate (recalls returning >= 1 result) and mean composite score from metrics. Score = weighted average of hit_rate (0.6) and normalized_mean_score (0.4). Falls back to 1.0 if no recall data exists yet.
  - **Freshness**: Based on entry age distribution relative to tier half-lives. Score = fraction of entries within 2x their tier's half-life.
  - **Completeness**: Based on field population rates (tags, relations, source, valid_at). Score = mean population rate across checked fields.
  - **Duplication**: Inverse of consolidation candidate ratio from `health()`. Score = 1.0 - (consolidation_candidates / entry_count).
  - **Staleness**: Based on fraction of entries flagged as stale but not yet archived. Score = 1.0 - (stale_served_count / entry_count).
  - **Integrity**: Based on `verify_integrity()` results. Score = verified_count / (verified_count + tampered_count + no_hash_count).
- [ ] `DiagnosticsReport` model: `timestamp`, `composite_score` (weighted average), `grade`, `dimensions` (list of DimensionScore), `anomalies` (list of str), `recommendations` (list of str)
- [ ] Composite scoring: configurable weights via `DiagnosticsConfig` (defaults: retrieval=0.25, freshness=0.20, completeness=0.15, duplication=0.15, staleness=0.15, integrity=0.10). Weights auto-normalized to sum=1.0 when custom dimensions are added
- [ ] **Correlation down-weighting**: if two dimensions have Pearson correlation > 0.7 over the last 20+ history snapshots, their combined weight is reduced by 30% (redistributed to uncorrelated dimensions) to avoid double-counting
- [ ] Unit tests for each dimension scoring function with edge cases (empty store, full store, all stale, etc.)

---

### STORY-030.2: Anomaly detection

**Status:** planned
**Effort:** M
**Depends on:** STORY-030.1
**Context refs:** `src/tapps_brain/diagnostics.py`, `src/tapps_brain/metrics.py`
**Verification:** `pytest tests/unit/test_diagnostics.py::TestAnomalyDetection -v`

#### Why

Quality scores are point-in-time snapshots. Anomaly detection identifies *changes* — when a dimension that was healthy suddenly degrades. Research (JMP, Ross et al. 2012) shows that EWMA (Exponentially Weighted Moving Average) outperforms simple thresholds for slowly-changing metrics with small sample sizes, detecting 1-sigma shifts in ~10 samples vs ~44 for Shewhart charts. EWMA requires O(1) memory and compute per observation — ideal for tapps-brain's lazy evaluation pattern.

#### Acceptance Criteria

- [ ] `AnomalyDetector` class in `diagnostics.py` using EWMA-based drift detection
- [ ] EWMA parameters: `lambda_param=0.2` (smoothing factor), `min_observations=20` (self-start: first 20 observations establish baseline mean and sigma)
- [ ] **Dual-threshold detection** per dimension:
  - Warning: EWMA statistic exceeds 2-sigma control limit → `threshold_warning` anomaly
  - Critical: EWMA statistic exceeds 3-sigma control limit → `threshold_critical` anomaly
- [ ] **Confirmation window**: anomaly must persist for 3 consecutive observations before alerting (reduces false positives from transient spikes)
- [ ] EWMA state stored in diagnostics history (no separate state — recomputed from history on startup)
- [ ] Each anomaly includes: dimension, current score, EWMA statistic, control limit, human-readable description
- [ ] Anomalies appended to `DiagnosticsReport.anomalies` list
- [ ] Recommendations generated based on anomaly type:
  - Low freshness → "Consider running decay sweep or ingesting fresh content"
  - High duplication → "Run consolidation: `store.consolidate()`"
  - High staleness → "Run garbage collection: `store.gc()`"
  - Low integrity → "Investigate tampered entries: `store.verify_integrity()`"
  - Low retrieval effectiveness → "Review scoring weights or check for knowledge gaps"
- [ ] Unit tests: inject degraded metrics sequences, verify EWMA triggers at correct points, verify confirmation window prevents single-spike false alarms

---

### STORY-030.3: Quality history and trend tracking

**Status:** planned
**Effort:** M
**Depends on:** STORY-030.1
**Context refs:** `src/tapps_brain/diagnostics.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_diagnostics.py::TestQualityHistory -v`

#### Why

A single scorecard is useful; a trend over time is powerful. Storing scorecard snapshots enables EWMA drift detection (STORY-030.2), correlation analysis (STORY-030.1), trend visualization, and regression analysis. Storage is lightweight — one row per snapshot with dimension scores as JSON.

#### Acceptance Criteria

- [ ] New `diagnostics_history` SQLite table: `id`, `timestamp`, `composite_score`, `dimensions_json` (serialized DimensionScore list), `anomalies_json`
- [ ] `DiagnosticsStore.record(report: DiagnosticsReport)` — persists a snapshot
- [ ] `DiagnosticsStore.history(since=None, until=None, limit=100) -> list[DiagnosticsReport]` — retrieves historical snapshots
- [ ] `DiagnosticsStore.rolling_average(window_days=7) -> dict[str, float]` — computes rolling mean per dimension (used by EWMA baseline)
- [ ] Automatic recording: `store.diagnostics()` optionally records to history (configurable, default=True)
- [ ] Retention policy: snapshots older than `retention_days` (default 90) are pruned on each write
- [ ] Schema migration v8 → v9 (or bundled with EPIC-029's migration if co-developed)
- [ ] Unit tests for storage round-trip, rolling average calculation, and retention pruning

---

### STORY-030.4: Circuit breakers

**Status:** planned
**Effort:** M
**Depends on:** STORY-030.2
**Context refs:** `src/tapps_brain/diagnostics.py`, `src/tapps_brain/recall.py`, `src/tapps_brain/models.py`
**Verification:** `pytest tests/unit/test_diagnostics.py::TestCircuitBreakers -v`

#### Why

When quality degrades critically, the system should degrade gracefully rather than silently serve bad results. Research (Resilience4j, CDR framework, Hannecke 2025) shows that traditional 3-state circuit breakers are insufficient for AI systems because semantic failures (valid-looking but low-quality results) require a **DEGRADED** state between healthy and tripped. The 4-state model (CLOSED/DEGRADED/OPEN/HALF_OPEN) maintains service while limiting blast radius — "for user-facing agents, 'nothing' is often worse than 'degraded but transparent.'"

#### Acceptance Criteria

- [ ] `CircuitBreaker` class with 4 states:
  - **CLOSED** (healthy): composite score >= 0.6. Normal operation, no warnings.
  - **DEGRADED** (partial): composite score 0.3-0.6. Results returned with `quality_warning` describing degraded dimensions. Optional features (vector search, Hive federation) can be disabled to reduce blast radius.
  - **OPEN** (critical): composite score < 0.3. Results returned with prominent critical warning. Tier 1 auto-remediation triggers.
  - **HALF_OPEN** (recovery testing): after cooldown timeout (default 5 min), allows limited probe operations to test if quality has recovered. If probes succeed (3 consecutive diagnostics scores >= 0.6), transitions to CLOSED. If any probe fails, transitions back to OPEN with reset cooldown.
- [ ] `RecallResult.quality_warning` field: `None` when CLOSED (zero overhead in happy path), descriptive string when DEGRADED or OPEN
- [ ] **Tier 1 auto-remediation** triggers in OPEN state (configurable, default enabled):
  - Duplication score < 0.5 → auto-trigger `consolidate()` (max once per hour)
  - Staleness score < 0.5 → auto-trigger `gc()` (max once per hour)
  - Integrity score < 0.8 → log `integrity_alert` event (no auto-fix — requires human review)
- [ ] Rate limiting on auto-remediation with per-action cooldown to prevent loops
- [ ] **Graduated re-enablement** in HALF_OPEN: probe count configurable (default 3), with jitter on cooldown timeout to prevent thundering herd in multi-store scenarios
- [ ] Circuit breaker state transitions logged to audit trail
- [ ] Unit tests for each state transition, warning injection, auto-remediation triggers with cooldown, and HALF_OPEN recovery/failure paths

---

### STORY-030.5: Feedback-aware scoring (optional enhancement)

**Status:** planned
**Effort:** S
**Depends on:** STORY-030.1, EPIC-029 (STORY-029.1)
**Context refs:** `src/tapps_brain/diagnostics.py`, `src/tapps_brain/feedback.py`
**Verification:** `pytest tests/unit/test_diagnostics.py::TestFeedbackScoring -v`

#### Why

When EPIC-029 feedback data is available, the retrieval effectiveness dimension can use actual quality signals instead of proxy metrics (hit rate, score distribution). This story upgrades the scoring to incorporate feedback when the feedback table exists.

#### Acceptance Criteria

- [ ] Retrieval effectiveness scoring checks for `feedback_events` table existence
- [ ] If feedback data available: score incorporates feedback rating distribution (helpful=1.0, partial=0.5, irrelevant=0.0, outdated=0.0) weighted at 0.4, with hit_rate at 0.35 and mean_score at 0.25
- [ ] If no feedback data: falls back to original hit_rate + mean_score formula (backward compatible)
- [ ] Knowledge gap count from feedback surfaced in recommendations: "N knowledge gaps reported — review with `store.query_feedback(event_type='gap_reported')`"
- [ ] Unit tests for both paths (with and without feedback data)

---

### STORY-030.6: MCP and CLI exposure

**Status:** planned
**Effort:** M
**Depends on:** STORY-030.2, STORY-030.3
**Context refs:** `src/tapps_brain/mcp_server.py`, `src/tapps_brain/cli.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestDiagnosticsTools tests/unit/test_cli.py::TestDiagnosticsCommand -v`

#### Why

Diagnostics must be accessible from all three interfaces. MCP tools let LLMs check store quality mid-conversation. CLI lets operators run health assessments from scripts and dashboards.

#### Acceptance Criteria

- [ ] `diagnostics_report` MCP tool: returns full `DiagnosticsReport` as JSON (composite score, per-dimension scores, anomalies, recommendations, circuit breaker state)
- [ ] `diagnostics_history` MCP tool: accepts `since`, `until`, `limit`; returns historical snapshots
- [ ] `memory://diagnostics` MCP resource: returns latest diagnostics report summary (composite score, grade, anomaly count, circuit breaker state)
- [ ] `tapps-brain diagnostics` CLI command: runs diagnostics and displays report
- [ ] `tapps-brain diagnostics --format json` for machine-readable output
- [ ] `tapps-brain diagnostics --format table` for human-readable table with color-coded grades (A=green, B=blue, C=yellow, D=orange, F=red) — uses status page component model: Operational/Degraded/Partial Outage/Major Outage
- [ ] `tapps-brain diagnostics history [--since <date>] [--limit <n>]` for trend data
- [ ] Unit tests for MCP tool responses and CLI output formatting

---

### STORY-030.7: Integration tests

**Status:** planned
**Effort:** M
**Depends on:** STORY-030.4, STORY-030.6
**Context refs:** `tests/integration/`
**Verification:** `pytest tests/integration/test_diagnostics_integration.py -v`

#### Why

Validates the full diagnostics pipeline against a real store: scoring with actual data, EWMA-based anomaly detection across multiple diagnostics runs, 4-state circuit breaker transitions, and history persistence.

#### Acceptance Criteria

- [ ] Integration test: populate store with varied entries, run diagnostics, verify scores reflect actual data quality
- [ ] Integration test: degrade store quality (add many stale entries) across multiple diagnostics runs, verify EWMA-based anomaly detection triggers after confirmation window
- [ ] Integration test: run diagnostics multiple times, verify history persistence and rolling average
- [ ] Integration test: degrade below DEGRADED threshold, verify `RecallResult.quality_warning` populated
- [ ] Integration test: degrade below OPEN threshold, verify auto-remediation triggers and cooldown prevents re-trigger
- [ ] Integration test: verify HALF_OPEN → CLOSED recovery after quality improvement
- [ ] All tests use real `MemoryStore` + SQLite (no mocks)

---

### STORY-030.8: Custom quality dimensions

**Status:** planned
**Effort:** M
**Depends on:** STORY-030.1
**Context refs:** `src/tapps_brain/diagnostics.py`, `src/tapps_brain/_protocols.py`
**Verification:** `pytest tests/unit/test_diagnostics.py::TestCustomDimensions -v`

#### Why

Host projects embedding tapps-brain need to monitor their own quality dimensions beyond memory health. TheStudio might need `response_relevance`, `user_retention`, or `task_success_rate`. Following the Spring Boot Actuator and Great Expectations patterns, custom dimensions implement the `HealthDimension` Protocol and are registered at store initialization. This turns diagnostics into a reusable monitoring framework.

#### Acceptance Criteria

- [ ] `DiagnosticsConfig.custom_dimensions: list[HealthDimension]` — additional dimensions registered at `MemoryStore` init
- [ ] Custom dimensions implement `HealthDimension` Protocol: `name: str`, `check(store) -> DimensionScore`, `default_weight: float`
- [ ] Custom dimensions included in `DiagnosticsReport.dimensions` alongside built-in dimensions
- [ ] Composite score reweighted to include custom dimensions (all weights normalized to sum=1.0)
- [ ] Anomaly detection (EWMA) and circuit breakers apply to custom dimensions — no special-casing
- [ ] `DiagnosticsConfig` also accepts `custom_dimensions` via profile YAML (using entry points or importable dotted paths for the scorer class)
- [ ] Example in tests: a `ResponseLatencyDimension` that scores based on a custom metric
- [ ] Unit tests for registration, scoring inclusion, weight normalization, and anomaly detection on custom dimensions

---

### STORY-030.9: Per-namespace Hive diagnostics

**Status:** planned
**Effort:** M
**Depends on:** STORY-030.1, EPIC-011 (Hive)
**Context refs:** `src/tapps_brain/diagnostics.py`, `src/tapps_brain/hive.py`
**Verification:** `pytest tests/unit/test_diagnostics.py::TestHiveDiagnostics -v`

#### Why

When Hive is enabled, a degraded Hive namespace can silently poison local recall quality (Hive results are merged with local at configurable weight, default 0.8). Per-namespace health monitoring — following the Kubernetes namespace-scoped health pattern and Nobl9 per-tenant SLO research — ensures that aggregate Hive health doesn't mask individual namespace problems. Aggregate SLOs can mask individual tenants experiencing 5.9% error rate while overall shows 99.9%.

#### Acceptance Criteria

- [ ] `DiagnosticsReport.hive_diagnostics: dict[str, list[DimensionScore]] | None` — per-namespace quality scores (None when Hive disabled)
- [ ] Per-namespace dimensions:
  - **Hive freshness**: entry age distribution within the namespace
  - **Hive duplication**: cross-entry overlap within the namespace
  - **Hive feedback score**: if EPIC-029 federated feedback available, aggregate rating distribution for namespace entries
- [ ] `hive_composite_score: float | None` — aggregate across all namespaces using **worst-of semantics** (if any namespace is unhealthy, Hive health reflects it)
- [ ] Circuit breaker can dynamically reduce `hive.recall_weight` when Hive quality degrades (e.g., from 0.8 → 0.4 in DEGRADED state, → 0.0 in OPEN state) instead of binary Hive on/off
- [ ] Only computed when Hive is enabled (zero cost otherwise)
- [ ] Backward-compatible: `DiagnosticsReport` remains valid without Hive fields
- [ ] Unit tests for per-namespace scoring, worst-of aggregation, and dynamic recall weight adjustment

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-030.1 — Quality dimensions and scoring | M | Foundation: scoring model + Protocol |
| 2 | STORY-030.3 — Quality history | M | Can parallel with 030.8; needed by 030.2 |
| 3 | STORY-030.8 — Custom quality dimensions | M | Can parallel with 030.3; enables host projects early |
| 4 | STORY-030.2 — Anomaly detection (EWMA) | M | Depends on 030.1 + 030.3 (for history) |
| 5 | STORY-030.4 — Circuit breakers (4-state) | M | Depends on 030.2 |
| 6 | STORY-030.5 — Feedback-aware scoring | S | Optional; depends on EPIC-029 |
| 7 | STORY-030.9 — Hive namespace diagnostics | M | Depends on 030.1; requires Hive |
| 8 | STORY-030.6 — MCP and CLI exposure | M | Depends on 030.2 + 030.3 |
| 9 | STORY-030.7 — Integration tests | M | Final validation |

## Dependency Graph

```
030.1 (scoring + Protocol) ──┬──→ 030.3 (history) ──→ 030.2 (EWMA anomaly) ──→ 030.4 (circuit breakers) ──┐
                             │                                                                              │
                             ├──→ 030.8 (custom dimensions)                                                │
                             │                                                                              │
                             └──→ 030.9 (Hive diagnostics)                                                 │
                                                                                                            │
                             EPIC-029 ──→ 030.5 (feedback scoring)                                         │
                                                                                                            │
                             030.3 + 030.2 ──→ 030.6 (MCP + CLI) ──────────────────────────────────────────┼──→ 030.7
```

030.3 and 030.8 can be worked in parallel after 030.1. 030.9 is independent after 030.1 (requires Hive).
