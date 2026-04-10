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

### STORY-061.1: OTel instrumentation (core paths)

**Status:** planned  
**Size:** L  
**Depends on:** EPIC-059

#### Why

Without spans on hot paths, Postgres regressions are invisible until users complain.

#### Acceptance criteria

- [ ] Tracer spans for: `remember`, `recall`, hive propagate/search (names consistent with internal architecture doc).
- [ ] Metrics (histograms/counters): operation duration, error count, pool in-use connections, query counts **bounded cardinality** (no raw memory text as labels).
- [ ] W3C trace context propagated through optional HTTP adapter (EPIC-060).
- [ ] `tapps_lookup_docs`-aligned patterns for Python OTel SDK usage reviewed once during implementation.

#### Verification

- Integration test with in-memory OTLP collector or exporter mock.

---

### STORY-061.2: Health & readiness split

**Status:** planned  
**Size:** S  
**Depends on:** STORY-061.1

#### Why

Orchestrators need **liveness** vs **readiness** semantics.

#### Acceptance criteria

- [ ] `/health` or equivalent: process up (cheap).
- [ ] `/ready`: Postgres ping **+** migration version matches expected **or** explicit “degraded” JSON with reason.
- [ ] Documented behavior when DB is down (503 vs circuit).

#### Verification

- Container kill tests or integration mocks.

---

### STORY-061.3: Redaction & cardinality policy

**Status:** planned  
**Size:** M  
**Depends on:** STORY-061.1

#### Why

Observability must not become a data exfil channel.

#### Acceptance criteria

- [ ] Written policy: allowed span attributes; forbidden (content, secrets, PII).
- [ ] Log handler strips or hashes memory bodies; reviewed in PR checklist.
- [ ] Metric views drop high-cardinality labels (OpenTelemetry Views where applicable).

#### Verification

- Static scan or unit tests for log formatter.

---

### STORY-061.4: Operator runbook & dashboard template

**Status:** planned  
**Size:** S  
**Depends on:** STORY-061.1–061.3

#### Why

“First-class” means SRE can onboard in one sitting.

#### Acceptance criteria

- [ ] `docs/operations/` (or equivalent) runbook: alerts, dashboards, triage steps.
- [ ] Optional: Grafana JSON or Prometheus rules **as examples** (not mandatory vendor lock-in).

#### Verification

- Dry-run with a teammate not on the core team.

## Out of scope

- Proprietary APM agents as a requirement.
- Full GenAI semantic convention coverage in v3.0 (incremental alignment with EPIC-032).

## References

- [EPIC-032](EPIC-032.md) (OTel GenAI semantic conventions — align incrementally)
- [EPIC-059](EPIC-059.md) — Postgres-only persistence (foundation; blocks this epic)
- `docs/engineering/system-architecture.md`
