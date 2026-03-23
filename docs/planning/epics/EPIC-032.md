---
id: EPIC-032
title: "OTel GenAI semantic conventions — standardized telemetry export"
status: planned
priority: low
created: 2026-03-23
tags: [opentelemetry, telemetry, genai, observability, integration]
---

# EPIC-032: OTel GenAI Semantic Conventions — Standardized Telemetry Export

## Context

EPIC-007 added an optional OpenTelemetry exporter (`otel_exporter.py`) that converts tapps-brain's internal metrics to OTel format. However, this predates the OpenTelemetry GenAI Semantic Conventions (semconv v1.40.0, Feb 2025) and the MCP Semantic Conventions (semconv v1.35.0, Jun 2024) that are now the industry standard for AI system observability.

**Key spec developments since EPIC-007:**

1. **GenAI retrieval conventions** (Development stability): `gen_ai.operation.name = "retrieval"` with a formal JSON schema for retrieval documents (`id` + `score`, extensible via `additionalProperties: true`). Attribute `gen_ai.retrieval.query.text` is opt-in (sensitive). No "memory" operation type exists yet — Issue #2664 proposes `gen_ai.memory.*` but remains a proposal.

2. **MCP semantic conventions** (Development stability, semconv v1.35.0): Full span and metric definitions for MCP tool calls. Key attributes: `mcp.method.name` (e.g., `tools/call`), `mcp.session.id`, `gen_ai.tool.name`. W3C Trace Context propagated via `params._meta`. MCP metrics: `mcp.client.operation.duration` and `mcp.server.operation.duration` histograms.

3. **Privacy controls**: The `opentelemetry-util-genai` package defines `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` env var with values `NO_CONTENT` (default), `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT`. All content attributes are `opt_in` requirement level.

tapps-brain's MCP server already handles 41+ tool invocations. By adopting both GenAI and MCP semantic conventions, tapps-brain becomes plug-and-play compatible with Grafana, Datadog, New Relic, Honeycomb, or self-hosted Jaeger/Tempo stacks. Custom metrics use the `tapps_brain.*` namespace prefix per OTel naming conventions.

This epic is intentionally small and optional. It upgrades the existing OTel exporter to convention-aware traces and metrics. It does not add required dependencies. All GenAI semconv attributes are "Development" stability — use requires `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.

## Success Criteria

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

### STORY-032.1: GenAI and MCP span instrumentation

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/mcp_server.py`, `src/tapps_brain/_feature_flags.py`
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestGenAISpans -v`

#### Why

The GenAI retrieval and MCP semantic conventions define exactly how retrieval operations and MCP tool calls should be traced. Adopting both means any user who points an OTel collector at tapps-brain gets properly labeled spans that their observability platform understands natively. The MCP conventions (semconv v1.35.0) are particularly important since tapps-brain's primary external interface is MCP.

#### Acceptance Criteria

- [ ] Upgrade `otel_exporter.py` to create tracer with convention-compliant attributes
- [ ] **MCP tool call spans** (for every MCP tool invocation):
  - Span name: `{mcp.method.name} {gen_ai.tool.name}` (e.g., `tools/call memory_recall`)
  - Span kind: `SERVER` (tapps-brain is the MCP server)
  - `mcp.method.name`: `"tools/call"` for tool invocations, `"resources/read"` for resource reads
  - `mcp.session.id`: from MCP session context (if available)
  - `mcp.protocol.version`: MCP protocol version
  - `gen_ai.tool.name`: the specific tool name (e.g., `memory_recall`, `memory_save`)
  - `gen_ai.operation.name`: maps tool to GenAI operation: `memory_recall`/`memory_search` → `"retrieval"`, `memory_save` → `"execute_tool"`, others → `"execute_tool"`
  - `gen_ai.data_source.id`: `"tapps_brain"`
- [ ] **Retrieval document events** within recall/search spans: each returned memory as a structured event following the official JSON schema: `{"id": "<entry_key>", "score": <composite_score>}` with `additionalProperties` for tier and staleness
- [ ] **W3C Trace Context propagation**: if `params._meta.traceparent` is present in MCP request, extract and use as parent span context
- [ ] Non-retrieval spans (save, delete, reinforce, consolidate, gc) use `gen_ai.operation.name = "execute_tool"` with `gen_ai.tool.name` set to the specific operation
- [ ] `OTelConfig` with settings: `enabled` (bool), `service_name` (str, default "tapps-brain")
- [ ] When `HAS_OTEL` is False, all span creation is a no-op (zero overhead via null-object pattern)
- [ ] Unit tests with mocked OTel SDK verifying span structure, attribute names, and document events match spec

---

### STORY-032.2: GenAI and MCP metric conventions

**Status:** planned
**Effort:** S
**Depends on:** STORY-032.1
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/metrics.py`
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestGenAIMetrics -v`

#### Why

The GenAI and MCP metric conventions define standard metric names that observability platforms recognize natively. Custom metrics use the `tapps_brain.*` vendor prefix per OTel naming guidance. Correct instrument selection (Counter vs Histogram vs Gauge) ensures metrics aggregate correctly across cardinality dimensions.

#### Acceptance Criteria

- [ ] **Standard GenAI metrics** (Histogram, unit=`s`):
  - `gen_ai.client.operation.duration` with attribute `gen_ai.operation.name` (retrieval, execute_tool) — maps from `recall_ms`, `save_ms`, `search_ms`
- [ ] **Standard MCP metrics** (Histogram, unit=`s`, buckets `[0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300]`):
  - `mcp.server.operation.duration` with attribute `mcp.method.name`
- [ ] **Standard token metric** (Histogram, unit=`{token}`):
  - `gen_ai.client.token.usage` — maps from recall token budget usage
- [ ] **Custom `tapps_brain.*` metrics** (vendor-prefixed):
  - `tapps_brain.entry.count` (UpDownCounter, by tier) — current entry count
  - `tapps_brain.consolidation.candidates` (Gauge) — from health report
  - `tapps_brain.gc.candidates` (Gauge) — from health report
  - `tapps_brain.feedback.count` (Counter, by event_type) — only when EPIC-029 available
  - `tapps_brain.diagnostics.composite_score` (Gauge) — only when EPIC-030 available
  - `tapps_brain.diagnostics.circuit_breaker_state` (Gauge, encoded: 0=closed, 1=degraded, 2=open, 3=half_open) — only when EPIC-030 available
- [ ] **Cardinality management**: metric attributes limited to low-cardinality values only (`gen_ai.operation.name`, `mcp.method.name`, `tier`, `event_type`). Never use `entry_key`, `query`, `session_id`, or `conversation_id` as metric attributes
- [ ] Export triggered on `MetricsCollector.snapshot()` when OTel exporter is configured
- [ ] Unit tests verifying metric names, types (Counter/Histogram/Gauge/UpDownCounter), units, and attribute conformance

---

### STORY-032.3: Feedback and diagnostics events

**Status:** planned
**Effort:** S
**Depends on:** STORY-032.1, EPIC-029 (optional), EPIC-030 (optional)
**Context refs:** `src/tapps_brain/otel_exporter.py`, `src/tapps_brain/feedback.py`, `src/tapps_brain/diagnostics.py`
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestFeedbackDiagnosticsEvents -v`

#### Why

When EPIC-029 and EPIC-030 are available, their events are valuable telemetry signals. OTel Events are implemented as `LogRecord` with `event.name` attribute (per OTel event conventions). Feedback and diagnostics events make the full quality loop visible in observability platforms.

#### Acceptance Criteria

- [ ] Feedback events emitted as OTel Events (LogRecords with `event.name`):
  - `event.name = "tapps_brain.feedback.recall_rated"` with attributes: `rating`, `entry_keys` (if enabled)
  - `event.name = "tapps_brain.feedback.gap_reported"` with attributes: `description` (if enabled)
  - `event.name = "tapps_brain.feedback.issue_flagged"` with attributes: `entry_key`, `issue_type`
- [ ] Diagnostics events emitted as OTel Events:
  - `event.name = "tapps_brain.diagnostics.anomaly_detected"` with attributes: `dimension`, `score`, `threshold`, `anomaly_type`
  - `event.name = "tapps_brain.diagnostics.circuit_breaker_transition"` with attributes: `from_state`, `to_state`, `composite_score`
- [ ] All event names use `tapps_brain.*` namespace (not `gen_ai.*` since these are custom, not standardized)
- [ ] Events gracefully skipped when EPIC-029/030 modules are not available (feature detection via `hasattr`/try-import, not import errors)
- [ ] Unit tests verifying event emission with and without feedback/diagnostics modules

---

### STORY-032.4: Privacy controls and configuration

**Status:** planned
**Effort:** S
**Depends on:** STORY-032.1
**Context refs:** `src/tapps_brain/otel_exporter.py`
**Verification:** `pytest tests/unit/test_otel_exporter.py::TestPrivacy -v`

#### Why

Developer tool telemetry must be privacy-respecting by default. The OTel GenAI spec marks all content attributes as `opt_in` requirement level with sensitivity warnings. The `opentelemetry-util-genai` package defines `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` as the standard env var. tapps-brain should align with this convention while adding its own granular controls.

#### Acceptance Criteria

- [ ] **Standard OTel env var** respected: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` with values:
  - `NO_CONTENT` (default): no query text, no memory values, no feedback descriptions in spans or events
  - `SPAN_ONLY`: content in span attributes only
  - `EVENT_ONLY`: content in events only
  - `SPAN_AND_EVENT`: content in both
- [ ] **tapps-brain-specific env vars** for fine-grained control:
  - `TAPPS_BRAIN_OTEL_ENABLED` (bool, default False): master switch
  - `TAPPS_BRAIN_OTEL_LOG_ENTRY_KEYS` (bool, default True): whether entry keys appear (low sensitivity)
  - `TAPPS_BRAIN_OTEL_SERVICE_NAME` (str, default "tapps-brain"): service name in spans
- [ ] **Layered defense**: content omission happens at the application level (before data enters OTel pipeline), not via collector-level redaction — this is the primary defense per OTel security guidance
- [ ] When a content field is disabled, the attribute is omitted entirely (not redacted, not hashed, not replaced with placeholder)
- [ ] `OTelConfig` loadable from environment variables with fallback to profile YAML
- [ ] Unit tests for each privacy setting verifying attribute presence/absence across all content modes

---

### STORY-032.5: Integration tests

**Status:** planned
**Effort:** M
**Depends on:** STORY-032.1, STORY-032.2
**Context refs:** `tests/integration/`
**Verification:** `pytest tests/integration/test_otel_integration.py -v`

#### Why

Validates the full OTel pipeline: MCP tool calls producing convention-compliant spans, metrics exported with correct names and types, privacy controls enforced, and graceful degradation when OTel is not installed.

#### Acceptance Criteria

- [ ] Integration test: perform MCP recall via store, verify span has correct `gen_ai.operation.name`, `mcp.method.name`, `gen_ai.data_source.id`, and retrieval document events
- [ ] Integration test: perform multiple operations, export metrics, verify `gen_ai.client.operation.duration`, `mcp.server.operation.duration`, and `tapps_brain.*` custom metrics
- [ ] Integration test: set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT`, verify query text absent from spans
- [ ] Integration test: set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_AND_EVENT`, verify query text present
- [ ] Integration test: verify zero overhead and no errors when OTel is not installed (`HAS_OTEL=False`)
- [ ] Integration test: if EPIC-029/030 available, verify `tapps_brain.feedback.*` and `tapps_brain.diagnostics.*` events emitted
- [ ] All tests use real `MemoryStore` + mocked OTel collector (InMemorySpanExporter/InMemoryMetricReader — no actual network export)

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-032.1 — GenAI + MCP span instrumentation | M | Core: span instrumentation with both conventions |
| 2 | STORY-032.2 — GenAI + MCP metric conventions | S | Depends on 032.1; quick |
| 3 | STORY-032.4 — Privacy controls | S | Can parallel with 032.2 |
| 4 | STORY-032.3 — Feedback/diagnostics events | S | Optional; depends on 032.1 + other EPICs |
| 5 | STORY-032.5 — Integration tests | M | Final validation |

## Dependency Graph

```
032.1 (GenAI + MCP spans) ──┬──→ 032.2 (metrics) ──────────┐
                             │                               │
                             ├──→ 032.4 (privacy) ───────────┼──→ 032.5 (integration)
                             │                               │
                             └──→ 032.3 (feedback/diag) ─────┘
                                     ↑
                             EPIC-029 + EPIC-030 (optional)
```

032.2 and 032.4 can be worked in parallel after 032.1. 032.3 is optional and depends on other EPICs.
