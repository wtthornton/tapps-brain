---
id: EPIC-032
title: "OTel GenAI semantic conventions — standardized telemetry export"
status: planned
priority: low
created: 2026-03-23
tags: [opentelemetry, telemetry, genai, observability, integration]
see_also: [EPIC-061]
---

# EPIC-032: OTel GenAI Semantic Conventions — Standardized Telemetry Export

## Goal

Upgrade tapps-brain's optional OpenTelemetry exporter to comply with the OpenTelemetry GenAI and MCP semantic conventions, making tapps-brain plug-and-play with standard observability stacks (Grafana, Datadog, Honeycomb, Jaeger).

## Motivation

EPIC-007 shipped an OTel exporter before the GenAI semconv (v1.40.0, Feb 2025) and MCP semconv (v1.35.0, Jun 2024) were standardised. Without alignment, spans and metrics use non-standard names that observability platforms cannot interpret natively. Adopting both conventions now means any operator who points an OTel collector at tapps-brain gets correctly labelled signals with no extra configuration.

## Context

EPIC-007 added an optional OpenTelemetry exporter (`otel_exporter.py`) that converts tapps-brain's internal metrics to OTel format. However, this predates the OpenTelemetry GenAI Semantic Conventions (semconv v1.40.0, Feb 2025) and the MCP Semantic Conventions (semconv v1.35.0, Jun 2024) that are now the industry standard for AI system observability.

**Key spec developments since EPIC-007:**

1. **GenAI retrieval conventions** (Development stability): `gen_ai.operation.name = "retrieval"` with a formal JSON schema for retrieval documents (`id` + `score`, extensible via `additionalProperties: true`). Attribute `gen_ai.retrieval.query.text` is opt-in (sensitive). No "memory" operation type exists yet — Issue #2664 proposes `gen_ai.memory.*` but remains a proposal.

2. **MCP semantic conventions** (Development stability, semconv v1.35.0): Full span and metric definitions for MCP tool calls. Key attributes: `mcp.method.name` (e.g., `tools/call`), `mcp.session.id`, `gen_ai.tool.name`. W3C Trace Context propagated via `params._meta`. MCP metrics: `mcp.client.operation.duration` and `mcp.server.operation.duration` histograms.

3. **Privacy controls**: The `opentelemetry-util-genai` package defines `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` env var with values `NO_CONTENT` (default), `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT`. All content attributes are `opt_in` requirement level.

tapps-brain's MCP server already handles 54 tool invocations (current surface). By adopting both GenAI and MCP semantic conventions, tapps-brain becomes plug-and-play compatible with Grafana, Datadog, New Relic, Honeycomb, or self-hosted Jaeger/Tempo stacks. Custom metrics use the `tapps_brain.*` namespace prefix per OTel naming conventions.

This epic is intentionally small and optional. It upgrades the existing OTel exporter to convention-aware traces and metrics. It does not add required dependencies. All GenAI semconv attributes are "Development" stability — use requires `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.

## Acceptance Criteria

- [ ] MCP tool invocations emit OTel spans following both GenAI and MCP semantic conventions
- [ ] Span attributes include `gen_ai.operation.name`, `gen_ai.data_source.id`, `mcp.method.name`, `mcp.session.id`
- [ ] Recall operations emit structured retrieval document events (BEIR-schema-compatible: `id` + `score`)
- [ ] Feedback events (EPIC-029) and diagnostics (EPIC-030) emit OTel events when available
- [ ] OTel metrics use standard names: `gen_ai.client.operation.duration`, `mcp.server.operation.duration`, plus `tapps_brain.*` custom metrics
- [ ] Privacy controls align with `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` convention
- [ ] All OTel functionality remains optional (behind `HAS_OTEL` feature flag)
- [ ] Zero impact when OpenTelemetry is not installed
- [ ] Overall test coverage stays at 95%+

## Stories

### STORY-032.1: Tracer bootstrap and null-object when OTel off

**Status:** planned  
**Effort:** S  
**Depends on:** none  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/_feature_flags.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py` — tracer init tests

#### Why

All downstream stories assume a single place that creates the tracer and respects `HAS_OTEL`.

#### Acceptance Criteria

- [ ] Upgrade `otel_exporter.py` to obtain `TracerProvider` / tracer with service name from `OTelConfig`.
- [ ] `OTelConfig`: `enabled` (bool), `service_name` (str, default `"tapps-brain"`).
- [ ] When `HAS_OTEL` is False: null-object / no-op tracer; **zero** allocation on hot path (verified by trivial benchmark or import-time check).
- [ ] Unit tests: OTel disabled → no spans created.

---

### STORY-032.2: MCP tool call spans (attributes and naming)

**Status:** planned  
**Effort:** M  
**Depends on:** STORY-032.1  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/mcp_server.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestGenAISpans -v`

#### Why

MCP is the primary external API; span names and attributes must match semconv v1.35.0.

#### Acceptance Criteria

- [ ] **MCP tool call spans** for every tool invocation:
  - Span name: `{mcp.method.name} {gen_ai.tool.name}` (e.g. `tools/call memory_recall`)
  - Span kind: `SERVER`
  - `mcp.method.name`: `tools/call` vs `resources/read`
  - `mcp.session.id`, `mcp.protocol.version` when available
  - `gen_ai.tool.name`, `gen_ai.operation.name` mapping (retrieval vs `execute_tool`)
  - `gen_ai.data_source.id`: `"tapps_brain"`
- [ ] Unit tests with mocked OTel SDK: attribute keys and span names.

---

### STORY-032.3: Retrieval document events and W3C traceparent

**Status:** planned  
**Effort:** M  
**Depends on:** STORY-032.2  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/mcp_server.py`  
**Verification:** same as 032.2 — extended test class

#### Why

Retrieval quality is proven with per-document `id` + `score` events; hosts pass distributed trace headers.

#### Acceptance Criteria

- [ ] Within recall/search spans: structured events per result: `{"id": "<entry_key>", "score": <composite_score>}` + optional tier/staleness in `additionalProperties`.
- [ ] If `params._meta.traceparent` present: extract and use as parent span context.
- [ ] Unit tests: events emitted; parent context linked when traceparent set.

---

### STORY-032.4: Non-retrieval spans (save, delete, reinforce, consolidate, gc)

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-032.1  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/mcp_server.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestGenAISpans -v`

#### Why

Smaller than retrieval spans; separate story keeps PRs reviewable.

#### Acceptance Criteria

- [ ] Non-retrieval operations use `gen_ai.operation.name = "execute_tool"` and `gen_ai.tool.name` = concrete tool/operation name.
- [ ] Coverage for at least: save, delete, reinforce (others as time permits in same PR or follow-up micro-PR).
- [ ] Unit tests per operation type or parameterized table.

---

### STORY-032.5: Standard GenAI and MCP metrics

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-032.1  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/metrics.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestGenAIMetrics -v`

#### Why

Standard histogram names unblock Grafana dashboards; separate from custom vendor metrics.

#### Acceptance Criteria

- [ ] `gen_ai.client.operation.duration` (Histogram, `s`) with `gen_ai.operation.name`.
- [ ] `mcp.server.operation.duration` (Histogram, `s`, documented buckets) with `mcp.method.name`.
- [ ] `gen_ai.client.token.usage` (Histogram, `{token}`) from recall token budget when available.
- [ ] Unit tests: instrument names, units, attribute keys.

---

### STORY-032.6: Custom `tapps_brain.*` metrics and export hook

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-032.5  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/metrics.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestGenAIMetrics -v`

#### Why

Vendor metrics stay separate from semconv names; cardinality rules are easy to regress.

#### Acceptance Criteria

- [ ] Custom metrics: `tapps_brain.entry.count`, consolidation/GC gauges, feedback/diagnostics counters/gauges when modules present (per original epic list).
- [ ] **Cardinality:** never `entry_key`, `query`, `session_id` as labels.
- [ ] Export on `MetricsCollector.snapshot()` when OTel exporter configured.
- [ ] Unit tests: types (UpDownCounter/Gauge/Counter) and label sets.

---

### STORY-032.7: Feedback events as OTel Events

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-032.1  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/feedback.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestFeedbackDiagnosticsEvents -v`

#### Why

EPIC-029 (`feedback.py`) is shipped; feedback signals deserve isolated test coverage.

#### Acceptance Criteria

- [ ] Events: `tapps_brain.feedback.recall_rated`, `gap_reported`, `issue_flagged` with documented attributes.
- [ ] Skipped gracefully when feedback module unavailable (feature detection).
- [ ] Unit tests with/without feedback wired.

---

### STORY-032.8: Diagnostics events as OTel Events

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-032.1  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/diagnostics.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestFeedbackDiagnosticsEvents -v`

#### Why

EPIC-030 diagnostics are distinct from feedback; separate story avoids one huge test file.

#### Acceptance Criteria

- [ ] Events: `tapps_brain.diagnostics.anomaly_detected`, `circuit_breaker_transition` with attributes.
- [ ] Skipped when diagnostics unavailable.
- [ ] Unit tests mirroring 032.7 pattern.

---

### STORY-032.9: Privacy controls and OTelConfig from environment

**Status:** planned  
**Effort:** S  
**Depends on:** STORY-032.1  
**Context refs:** `src/tapps_brain/otel_exporter.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestPrivacy -v`

#### Why

Privacy is cross-cutting; implementing after span shapes avoids rework.

#### Acceptance Criteria

- [ ] `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`: `NO_CONTENT` | `SPAN_ONLY` | `EVENT_ONLY` | `SPAN_AND_EVENT`.
- [ ] `TAPPS_BRAIN_OTEL_ENABLED`, `TAPPS_BRAIN_OTEL_LOG_ENTRY_KEYS`, `TAPPS_BRAIN_OTEL_SERVICE_NAME` (or merged into `OTelConfig`).
- [ ] Disabled content: attribute **omitted**, not placeholder.
- [ ] `OTelConfig` from env with profile YAML fallback.
- [ ] Unit tests per mode: attribute presence/absence.

---

### STORY-032.10: End-to-end integration tests

**Status:** planned  
**Effort:** M  
**Depends on:** STORY-032.3, STORY-032.4, STORY-032.6, STORY-032.7, STORY-032.8, STORY-032.9  
**Context refs:** `tests/integration/`  
**Verification:** `pytest tests/integration/test_otel_integration.py -v`

#### Why

Validates pipeline: MCP → spans → metrics → privacy; catches integration gaps.

#### Acceptance Criteria

- [ ] Recall path: span attributes + document events (InMemorySpanExporter).
- [ ] Multi-op metrics: `gen_ai.*`, `mcp.*`, `tapps_brain.*` in InMemoryMetricReader.
- [ ] Privacy: `NO_CONTENT` vs `SPAN_AND_EVENT` behavior.
- [ ] `HAS_OTEL=False`: no import error, no overhead path exercised.
- [ ] Optional: feedback/diagnostics events when modules loaded.
- [ ] Real `MemoryStore` + mocked OTel backends (no network export).

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-032.1 — Tracer bootstrap | S | Foundation |
| 2 | STORY-032.2 — MCP spans | M | Core visibility |
| 3 | STORY-032.3 — Retrieval events + traceparent | M | Depends on 032.2 |
| 4 | STORY-032.4 — Non-retrieval spans | S | Parallel after 032.1 |
| 5 | STORY-032.5 — Standard metrics | S | Parallel after 032.1 |
| 6 | STORY-032.6 — Custom metrics | S | After 032.5 |
| 7 | STORY-032.9 — Privacy | S | Can parallel with 032.5–032.6 after 032.1 |
| 8 | STORY-032.7 — Feedback events | S | After 032.1 |
| 9 | STORY-032.8 — Diagnostics events | S | After 032.1 |
| 10 | STORY-032.10 — Integration tests | M | Last |

## Dependency Graph

```
032.1 ──┬──→ 032.2 ──→ 032.3 ─────────────────┐
        ├──→ 032.4 ─────────────────────────┼──→ 032.10
        ├──→ 032.5 ──→ 032.6 ───────────────┤
        ├──→ 032.7 ────────────────────────┤
        ├──→ 032.8 ────────────────────────┤
        └──→ 032.9 ───────────────────────┘
```

032.4 can proceed in parallel with 032.2 after 032.1. 032.10 gates on semantic completion of spans, metrics, feedback/diagnostics, and privacy.

## References

- [EPIC-061](EPIC-061.md) — Greenfield v3 observability (incremental alignment target)
- `src/tapps_brain/otel_exporter.py`
- `src/tapps_brain/mcp_server.py`
- `src/tapps_brain/metrics.py`
