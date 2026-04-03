---
id: EPIC-007
title: "Observability — metrics, audit trail queries, and health checks"
status: done
priority: medium
created: 2026-03-19
target_date: 2026-05-30
completed: 2026-03-21
tags: [observability, metrics, audit, monitoring]
---

# EPIC-007: Observability — Metrics, Audit Trail Queries, and Health Checks

## Progress in tree (2026-03-20)

Partial implementation exists and is covered by tests:

- `src/tapps_brain/metrics.py` — `MetricsCollector`, `MetricsSnapshot`, `StoreHealthReport`
- `MemoryStore.get_metrics()` — snapshot of in-process collector (instrumentation of every operation is **not** complete; see STORY-007.2)
- `MemoryStore.health()` — structured report (counts, schema version, tiers, consolidation/GC hints, federation summary)
- CLI: `tapps-brain maintenance health` (includes `profile_seed_version` when profile sets `seeding.seed_version`), `tapps-brain store metrics` (Typer)

Remaining per this epic: audit query API on JSONL, broad instrumentation, optional OpenTelemetry, and closing acceptance criteria below.

## Context

tapps-brain has a JSONL audit log (`memory_log.jsonl`) and uses `structlog` for event logging, but there is no structured observability:

- **No metrics**: no counters for saves, searches, recalls, consolidations, or GC runs. No latency histograms. Operators can't answer "how many recalls happened today?" or "what's the p95 search latency?" without parsing raw logs.
- **No audit trail API**: the JSONL log exists but is write-only. There's no way to query it programmatically ("show all changes to key X" or "what happened between 10am and 11am?").
- **No health checks**: no API to ask "is this store healthy?" (entry count vs. limit, consolidation backlog, federation sync status, schema version).

With EPIC-003 (auto-recall) and EPIC-004 (bi-temporal versioning) adding significant new complexity, operators need visibility into how the system behaves. This is especially important for federated deployments where multiple projects share memories.

This epic adds a zero-dependency metrics layer (in-memory counters/histograms), an audit trail query API, and a health check method — all deterministic, no external services required. An optional OpenTelemetry exporter is included for teams that want to integrate with existing monitoring infrastructure.

## Success Criteria

- [x] `store.get_metrics()` returns structured counters and histograms for all core operations
- [x] `store.audit(key=..., event_type=..., since=..., until=...)` queries the JSONL audit log
- [x] `store.health()` returns a structured health report (entry counts, schema version, federation status, consolidation state)
- [x] Metrics are zero-cost when not read (lazy computation, no per-operation overhead beyond incrementing a counter)
- [x] Optional OpenTelemetry exporter (behind feature flag, no required dependency)
- [x] Overall coverage stays at 95%+

## Stories

### STORY-007.1: In-memory metrics collector

**Status:** done
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_metrics.py -v --cov=tapps_brain.metrics --cov-report=term-missing`

#### Why

A lightweight metrics layer is the foundation for all observability. It must be zero-dependency (no Prometheus client, no StatsD) and low-overhead — just atomic counters and simple histograms that are incremented inline.

#### Acceptance Criteria

- [x] New `src/tapps_brain/metrics.py` module with `MetricsCollector` class
- [x] Counter support: `increment(name, value=1, tags=None)` — thread-safe via `threading.Lock`
- [x] Histogram support: `observe(name, value, tags=None)` — stores min/max/mean/p50/p95/p99 using reservoir sampling
- [x] `snapshot() -> MetricsSnapshot` — returns a frozen copy of all counters and histograms
- [x] `reset()` — clears all metrics (for testing)
- [x] `MetricsSnapshot` is a Pydantic model, serializable to JSON
- [x] No external dependencies
- [x] Unit tests for thread safety (concurrent increments from multiple threads)

---

### STORY-007.2: Instrument core operations

**Status:** done
**Effort:** L
**Depends on:** STORY-007.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/recall.py`, `src/tapps_brain/consolidation.py`, `src/tapps_brain/gc.py`
**Verification:** `pytest tests/unit/test_memory_store.py::TestMetrics -v`

#### Why

Metrics are only useful if the core paths emit them. This story instruments save, get, search, recall, consolidate, and GC with counters and latency histograms.

#### Acceptance Criteria

- [x] `MemoryStore` creates a `MetricsCollector` instance (shared with recall orchestrator)
- [x] `store.get_metrics() -> MetricsSnapshot` convenience method
- [x] Instrumented operations with counters: `store.save` (count), `store.get` (count, hits, misses), `store.search` (count, result_count), `store.recall` (count, token_count), `store.supersede` (count), `store.consolidate` (count, merged_count), `store.gc` (count, archived_count)
- [x] Instrumented operations with latency histograms: `save_ms`, `get_ms`, `search_ms`, `recall_ms`
- [x] Overhead < 0.1ms per operation (just counter increment)
- [x] Unit test: perform 100 saves, verify `get_metrics().counters["store.save"]` == 100
- [x] Unit test: verify latency histogram has reasonable p50/p95 values

---

### STORY-007.3: Audit trail query API

**Status:** done
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_audit.py -v --cov=tapps_brain.audit --cov-report=term-missing`

#### Why

The JSONL audit log is write-only today. Operators and integrators need to query it — "what happened to key X?", "show all supersessions in the last hour", "how many entries were GC'd this week?".

#### Acceptance Criteria

- [x] New `src/tapps_brain/audit.py` module with `AuditReader` class
- [x] `query(key=None, event_type=None, since=None, until=None, limit=100) -> list[AuditEntry]`
- [x] `AuditEntry` model: `timestamp`, `event_type`, `key`, `details` (dict)
- [x] Reads from the existing JSONL file (no schema change needed)
- [x] Efficient: uses seek/readline, doesn't load entire file into memory
- [x] `store.audit(**kwargs)` convenience method that delegates to `AuditReader`
- [x] Unit test: write 50 audit events, query by key, verify filtered results
- [x] Unit test: query by time range, verify correct windowing

---

### STORY-007.4: Health check API

**Status:** done
**Effort:** S
**Depends on:** STORY-007.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`, `src/tapps_brain/federation.py`
**Verification:** `pytest tests/unit/test_memory_store.py::TestHealthCheck -v`

#### Why

A single `store.health()` call gives operators a snapshot of store state — entry count vs. limit, tier distribution, schema version, federation status, and whether consolidation or GC is needed. This is the "status page" for a memory store.

#### Acceptance Criteria

- [x] `store.health() -> HealthReport` method
- [x] `HealthReport` model with fields: `entry_count` (int), `max_entries` (int), `tier_distribution` (dict), `schema_version` (int), `store_path` (str), `federation_enabled` (bool), `federation_project_count` (int), `oldest_entry_age_days` (float), `consolidation_candidates` (int), `gc_candidates` (int)
- [x] Computed lazily (no background scanning)
- [x] `HealthReport` is a Pydantic model, serializable to JSON
- [x] Unit test: populate store, verify health report matches expected values

---

### STORY-007.5: Optional OpenTelemetry exporter

**Status:** done
**Effort:** M
**Depends on:** STORY-007.2
**Context refs:** `src/tapps_brain/_feature_flags.py`, `pyproject.toml`
**Verification:** `pytest tests/unit/test_otel_exporter.py -v`

#### Why

Teams with existing monitoring infrastructure (Grafana, Datadog, New Relic) want to pipe tapps-brain metrics into their observability stack. OpenTelemetry is the standard. This must be optional — no required dependency.

#### Acceptance Criteria

- [x] New optional dependency group: `[project.optional-dependencies] otel = ["opentelemetry-api", "opentelemetry-sdk"]`
- [x] `src/tapps_brain/otel_exporter.py` module with `OTelExporter` class
- [x] `OTelExporter.export(snapshot: MetricsSnapshot)` — converts to OTel metrics
- [x] Feature flag: `HAS_OTEL` in `_feature_flags.py`, lazy detection
- [x] `MetricsCollector` accepts an optional `exporter` callback — called on each `snapshot()`
- [x] When `opentelemetry` is not installed, the exporter is silently unavailable
- [x] Unit test: mock OTel SDK, verify metrics are exported in correct format
- [x] Unit test: verify graceful behavior when OTel is not installed

---

### STORY-007.6: Integration tests and CLI integration

**Status:** done
**Effort:** M
**Depends on:** STORY-007.2, STORY-007.3, STORY-007.4
**Context refs:** `src/tapps_brain/store.py`
**Verification:** `pytest tests/integration/test_observability_integration.py -v`

#### Why

Validates the full observability stack with a real store — metrics accumulation across operations, audit trail queries after real mutations, and health checks reflecting actual store state.

#### Acceptance Criteria

- [x] Integration test: perform 50 mixed operations (save, search, recall, supersede), verify metrics snapshot reflects all of them
- [x] Integration test: perform mutations, query audit trail, verify correct event sequence
- [x] Integration test: populate store near capacity, verify health report flags consolidation/GC candidates
- [x] If EPIC-005 (CLI) is done: `tapps-brain store stats` includes metrics summary
- [x] All tests use real `MemoryStore` + SQLite (no mocks)

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-007.1 — Metrics collector | M | Foundation: counter/histogram infrastructure |
| 2 | STORY-007.3 — Audit trail API | M | Can parallel with 007.1 (independent) |
| 3 | STORY-007.2 — Instrument operations | L | Depends on 007.1; high-value |
| 4 | STORY-007.4 — Health check | S | Depends on 007.1; quick win |
| 5 | STORY-007.5 — OTel exporter | M | Optional; depends on 007.2 |
| 6 | STORY-007.6 — Integration tests | M | Final validation |

## Dependency Graph

```
007.1 (metrics) ──┬──→ 007.2 (instrument) ──┬──→ 007.5 (OTel) ──┐
                  │                          │                    │
                  └──→ 007.4 (health) ───────┘                   ├──→ 007.6 (integration)
                                                                 │
007.3 (audit trail) ─────────────────────────────────────────────┘
```

007.1 and 007.3 can be worked in parallel. 007.2 and 007.4 depend on 007.1. 007.5 depends on 007.2.
