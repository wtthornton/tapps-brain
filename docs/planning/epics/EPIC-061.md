---
id: EPIC-061
title: "Greenfield v3 — Observability-First Product (Simple & Complete)"
status: in_progress
priority: critical
created: 2026-04-10
updated: 2026-04-15
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

- [x] **OTLP** export configurable via env (`OTEL_EXPORTER_OTLP_ENDPOINT`). *(otel_tracer.py + otel_exporter.py)*
- [x] **Golden signals** for save, recall, hive, pool, migration. *(otel_tracer.py 535 lines; v3.6.0 label enrichment)*
- [x] Logs never emit raw memory content by default. *(redaction policy in observability.md)*
- [ ] **Operator runbook** (≤ 2 printed pages with dashboards + alert thresholds). *(docs/operations/ does not yet exist)*

## Stories

### STORY-061.1: Traces — remember / recall / hive hot paths

**Status:** done  
**Size:** M  
**Depends on:** EPIC-059

#### Why

Span names must match architecture doc before metrics and cardinality work.

#### Acceptance criteria

- [x] Tracer spans on `remember`, `recall`, and hive hot paths. *(otel_tracer.py)*
- [x] Span kind and resource attributes: `service.name`, `service.version` from env.
- [x] Unit tests with `InMemorySpanExporter`. *(tests/unit/test_otel_tracer.py, test_otel_exporter.py)*

#### Verification

- Focused pytest module for spans only.

---

### STORY-061.2: Metrics — duration, errors, pool, bounded labels

**Status:** done  
**Size:** M  
**Depends on:** STORY-061.1

#### Why

Histograms and counters are separate from trace wiring; cardinality rules are critical.

#### Acceptance criteria

- [x] Histograms/counters: duration, errors, pool, query counts. *(otel_tracer.py + Prometheus endpoint)*
- [x] No raw memory text as metric labels — bounded label set (project_id, agent_id, tool, status). *(v3.6.0)*
- [x] Export path wired to Prometheus `/metrics` + OTLP.

#### Verification

- Unit tests for metric instruments + label keys.

---

### STORY-061.3: Trace context — HTTP adapter and OTel review

**Status:** done  
**Size:** S  
**Depends on:** STORY-061.1

#### Why

W3C propagation across EPIC-060 HTTP host must not break when OTel enabled.

#### Acceptance criteria

- [x] W3C `traceparent` propagated through HTTP adapter via ASGI middleware. *(http_adapter.py)*
- [x] Python OTel SDK patterns reviewed.
- [x] Integration test: request with trace header creates child span. *(tests/integration/test_otel_integration.py)*

#### Verification

- Single integration test file.

---

### STORY-061.4: Probes — liveness semantics

**Status:** done  
**Size:** XS  
**Depends on:** STORY-061.2

#### Why

Cheap `/health` must never block on DB.

#### Acceptance criteria

- [x] `/health` returns 200 if process up; no Postgres call. *(http_adapter.py)*
- [x] Documented for Kubernetes `livenessProbe` vs `readinessProbe`. *(docs/guides/hive-deployment.md)*

#### Verification

- HTTP test with DB stopped — liveness still 200.

---

### STORY-061.5: Probes — readiness and degraded mode

**Status:** done  
**Size:** S  
**Depends on:** STORY-061.2

#### Why

Readiness must encode migration and DB failures distinctly.

#### Acceptance criteria

- [x] `/ready`: Postgres ping + migration version, or JSON body with `degraded` reason. *(http_adapter.py + health_check.py)*
- [x] DB down → 503; documented in deployment guide.

#### Verification

- Tests with mocked DB failure vs migration mismatch.

---

### STORY-061.6: Policy doc — allowed vs forbidden telemetry

**Status:** done  
**Size:** S  
**Depends on:** STORY-061.2

#### Why

Written policy before log/metric code changes land everywhere.

#### Acceptance criteria

- [x] Policy documented: allowed span attributes; forbidden (memory body, secrets, PII). *(docs/guides/observability.md)*
- [x] Review slot in PR template for observability PRs.

#### Verification

- Doc PR + one reviewer sign-off.

---

### STORY-061.7: Enforcement — log handler and metric views

**Status:** done  
**Size:** M  
**Depends on:** STORY-061.6

#### Why

Policy without code is wishful; OTel Views drop bad labels.

#### Acceptance criteria

- [x] Log formatter strips/hashes memory bodies by default. *(otel_tracer.py redaction)*
- [x] OTel Views drop high-cardinality labels. *(bounded label set: project_id, agent_id, tool, status)*
- [x] Unit tests confirm forbidden strings absent from log records.

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
