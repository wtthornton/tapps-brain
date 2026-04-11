---
id: EPIC-061
title: "Greenfield v3 — Observability-First Product (Simple & Complete)"
status: planned
priority: critical
created: 2026-04-10
tags: [greenfield, observability, otel, metrics, v3]
depends_on: [EPIC-059]
blocks: []
see_also: [EPIC-032]
---

# EPIC-061: Greenfield v3 — Observability-First Product (Simple & Complete)

## Goal

Make **OpenTelemetry** traces and metrics the default observability path for save/recall/hive operations, with health/readiness split, redaction policy, and a short operator runbook so SRE can run the brain without bespoke scripts.

## Motivation

Postgres-backed, multi-agent systems fail in production without golden signals and pool visibility; memory content must never leak via logs or metric labels.

## Context

With Postgres-only and many agents, **operators must see** latency, errors, pool health, and migration state **without** shelling into the DB. Telemetry is **first-class product**: defaults on, low noise, redaction-safe. OpenTelemetry (traces + metrics + structured logs) is the **default** export path (see also EPIC-032 alignment).

## Acceptance Criteria

- [ ] **OTLP** export configurable via env (`OTEL_EXPORTER_OTLP_ENDPOINT`, resource attributes for `service.name` / `service.version`).
- [ ] **Golden signals** for: save, recall, hive round-trip, pool wait time, migration version.
- [ ] Logs **never** emit raw memory content by default (redaction or hash).
- [ ] A **single** operator runbook (≤ 2 printed pages) lists dashboards + alert thresholds.

## Stories

### STORY-061.1: Traces — remember / recall / hive hot paths

**Status:** planned  
**Size:** M  
**Depends on:** EPIC-059

#### Why

Span names must match architecture doc before metrics and cardinality work.

#### Acceptance criteria

- [ ] Tracer spans on `remember`, `recall`, and hive propagate/search (names aligned with `docs/engineering/system-architecture.md`).
- [ ] Span kind and resource attributes: `service.name`, `service.version` from env.
- [ ] Unit tests with `InMemorySpanExporter` or mock tracer.

#### Verification

- Focused pytest module for spans only.

---

### STORY-061.2: Metrics — duration, errors, pool, bounded labels

**Status:** planned  
**Size:** M  
**Depends on:** STORY-061.1

#### Why

Histograms and counters are separate from trace wiring; cardinality rules are critical.

#### Acceptance criteria

- [ ] Histograms/counters: operation duration, error count, pool in-use connections, query counts.
- [ ] **No** raw memory text, queries, or entry keys as metric labels (document allowed label set).
- [ ] Export path wired to existing metrics snapshot or periodic flush.

#### Verification

- Unit tests for metric instruments + label keys.

---

### STORY-061.3: Trace context — HTTP adapter and OTel review

**Status:** planned  
**Size:** S  
**Depends on:** STORY-061.1

#### Why

W3C propagation across EPIC-060 HTTP host must not break when OTel enabled.

#### Acceptance criteria

- [ ] W3C `traceparent` propagated through optional HTTP adapter (EPIC-060) when present.
- [ ] One-time review note: Python OTel SDK patterns vs `tapps_lookup_docs` (link or inline checklist).
- [ ] Integration test: request with trace header creates child span.

#### Verification

- Single integration test file.

---

### STORY-061.4: Probes — liveness semantics

**Status:** planned  
**Size:** XS  
**Depends on:** STORY-061.2

#### Why

Cheap `/health` must never block on DB.

#### Acceptance criteria

- [ ] `/health` (or shared helper) returns 200 if process up; **no** Postgres call.
- [ ] Documented for Kubernetes `livenessProbe` vs `readinessProbe`.

#### Verification

- HTTP test with DB stopped — liveness still 200.

---

### STORY-061.5: Probes — readiness and degraded mode

**Status:** planned  
**Size:** S  
**Depends on:** STORY-061.2

#### Why

Readiness must encode migration and DB failures distinctly.

#### Acceptance criteria

- [ ] `/ready`: Postgres ping + migration version matches expected **or** JSON body with `degraded` reason.
- [ ] Documented: DB down → 503 vs 500; link runbook snippet.

#### Verification

- Tests with mocked DB failure vs migration mismatch.

---

### STORY-061.6: Policy doc — allowed vs forbidden telemetry

**Status:** planned  
**Size:** S  
**Depends on:** STORY-061.2

#### Why

Written policy before log/metric code changes land everywhere.

#### Acceptance criteria

- [ ] Markdown policy: allowed span attributes; forbidden (memory body, secrets, PII).
- [ ] Review slot in PR template for observability PRs.

#### Verification

- Doc PR + one reviewer sign-off.

---

### STORY-061.7: Enforcement — log handler and metric views

**Status:** planned  
**Size:** M  
**Depends on:** STORY-061.6

#### Why

Policy without code is wishful; OTel Views drop bad labels.

#### Acceptance criteria

- [ ] Log formatter strips or hashes memory bodies by default.
- [ ] OpenTelemetry **Views** (or equivalent) drop high-cardinality labels on selected instruments.
- [ ] Static test or unit test: forbidden strings never appear in emitted log records in test harness.

#### Verification

- Unit tests for formatter + view registration.

---

### STORY-061.8: Operator runbook and example alerts

**Status:** planned  
**Size:** S  
**Depends on:** STORY-061.4, STORY-061.5, STORY-061.7

#### Why

SRE onboarding closes the epic.

#### Acceptance criteria

- [ ] `docs/operations/` runbook ≤ 2 printed pages: key metrics, alert thresholds, triage steps.
- [ ] Optional: example Prometheus rules or Grafana JSON **as non-normative examples**.

#### Verification

- Dry-run with teammate not on core team.

## Out of scope

- Proprietary APM agents as a requirement.
- Full GenAI semantic convention coverage in v3.0 (incremental alignment with EPIC-032).

## References

- [EPIC-032](EPIC-032.md) (OTel GenAI semantic conventions — align incrementally)
- [EPIC-059](EPIC-059.md) — Postgres-only persistence (foundation; blocks this epic)
- `docs/engineering/system-architecture.md`
