---
id: EPIC-060
title: "Greenfield v3 — Agent-First Core & Minimal Runtime API"
status: done
priority: critical
created: 2026-04-10
updated: 2026-04-27
tags: [greenfield, agent-first, api, v3, runtime]
depends_on: [EPIC-059]
blocks: []
---

# EPIC-060: Greenfield v3 — Agent-First Core & Minimal Runtime API

## Goal

Center all product documentation and integrations on an **agent-first** Python API; expose **minimal** HTTP (or gRPC) endpoints for health, readiness, metrics, and optional host hooks only—never a parallel REST memory API.

## Motivation

2026 agent stacks standardize on MCP and in-process SDKs; large HTTP surfaces duplicate semantics, increase attack surface, and drift from MCP. Minimal runtime endpoints keep trust and documentation tractable.

## Context

The **canonical product surface** is the **Agent** abstraction (`AgentBrain` or successor): `remember` / `recall` / `forget` / `learn_from_*`, driven by env-declared identity and groups. Any HTTP (or gRPC) server is a **thin host**: health, readiness, metrics, and **at most** a tiny set of orchestration endpoints—not a second full REST model of memory.

## Acceptance Criteria

> **2026-04-15 note:** EPIC-070 shipped a full REST API (`/v1/*`) alongside MCP transport. The "≤10 routes" target was superseded. The operative constraint going forward is **ADR-008**: no new HTTP endpoint without library + MCP parity. Stories 060.3–060.6 are complete.

- [x] **Agent-first** Python API (`AgentBrain`) is documented in `docs/guides/agent-integration.md`.
- [x] No new memory feature ships **HTTP-only** without **MCP + library** parity — enforced by ADR-008 and `epic-validation.yml`.
- [x] HTTP adapter exposes health/ready/metrics + full REST API justified by EPIC-070 / ADR-008.
- [x] Embeddable hosts (e.g. AgentForge) integration guide refreshed with v3 env contract and diagram. *(docs/guides/agentforge-integration.md — sequence diagram lines 14-35)*

## Stories

### STORY-060.1: Agent integration page — API surface

**Status:** done  
**Size:** S  
**Depends on:** EPIC-059 foundation (types stable)

#### Why

Hosts need one page listing public methods and env vars before exception taxonomy.

#### Acceptance criteria

- [x] **Agent integration** page lists public `AgentBrain` methods. *(docs/guides/agent-integration.md)*
- [x] Env vars table. *(docs/guides/postgres-dsn.md + .env.example)*
- [x] Cross-links from `README` / `AGENTS.md`.

#### Verification

- Doc PR review; link check.

---

### STORY-060.2: Agent integration page — exceptions and breaking changes

**Status:** done  
**Size:** S  
**Depends on:** STORY-060.1

#### Why

Typed errors and explicit v3 breaks reduce support load.

#### Acceptance criteria

- [x] Documented exception types: configuration vs transient DB vs validation (map to actual classes in code). *(docs/guides/agent-integration.md §"Exception taxonomy" — `BrainError` base + `BrainConfigError` / `BrainTransientError` / `BrainValidationError` mapped to `agent_brain.py:38-85`)*
- [x] Short **v3 breaking changes** subsection: renames allowed; no compatibility shim required (greenfield). *(docs/guides/agent-integration.md §"v3 breaking changes" — Postgres-only, removed classes, new env vars, no local DB files, migration path)*
- [x] Optional: mypy-public re-export list or `api` module snapshot. *(`src/tapps_brain/__init__.py` `__all__` includes `BrainConfigError`, `BrainError`, `BrainTransientError`)*

#### Verification

- Doc review + spot-check against `agent_brain.py`.

---

### STORY-060.3: HTTP adapter — liveness, readiness, metrics

**Status:** done  
**Size:** M  
**Depends on:** STORY-060.2

#### Why

Smallest useful HTTP surface: orchestrators need probes before optional hooks.

#### Acceptance criteria

- [x] **`/health`**: process up, no DB required. *(http_adapter.py)*
- [x] **`/ready`**: DB ping + migration version + degraded mode. *(http_adapter.py + health_check.py)*
- [x] **`/metrics`**: Prometheus text + OTel labels. *(http_adapter.py + otel_tracer.py)*

#### Verification

- Contract tests: HTTP status codes for healthy vs DB down (mocked).

---

### STORY-060.4: HTTP adapter — optional routes, auth, OpenAPI

**Status:** done  
**Size:** M  
**Depends on:** STORY-060.3

#### Why

At most two extra routes for host hooks; auth documented as mandatory in prod.

#### Acceptance criteria

- [x] Full REST API (`/v1/*`) + admin (`/admin/*`) behind bearer token auth. *(EPIC-070 / ADR-008)*
- [x] **Auth required in production** — `TAPPS_BRAIN_AUTH_TOKEN` + `TAPPS_BRAIN_ADMIN_TOKEN`.
- [x] OpenAPI spec at `/openapi.json` and `docs/guides/http-api.openapi.yaml`.

#### Verification

- OpenAPI snapshot test; fuzz 401/403 on protected routes.

---

### STORY-060.5: ADR — no new HTTP without MCP + library parity

**Status:** done  
**Size:** S  
**Depends on:** STORY-060.4

#### Why

Prevents endpoint creep across future PRs.

#### Acceptance criteria

- [x] ADR committed: no new public HTTP routes without **library + MCP** parity. *(ADR-008 — accepted)*
- [x] Linked from `docs/engineering/` index or architecture README.

#### Verification

- Maintainer approval on ADR PR.

---

### STORY-060.6: Guardrails — CI or CODEOWNERS for HTTP tree

**Status:** done  
**Size:** S  
**Depends on:** STORY-060.5

#### Why

Automation backs the ADR when humans forget.

#### Acceptance criteria

- [x] CI validation on v3 epics. *(.github/workflows/epic-validation.yml)*
- [x] Documented in contributing/release checklist.

#### Verification

- Dry-run PR that touches HTTP path triggers review rule.

---

### STORY-060.7: Host guide — Postgres-only rewrite (body)

**Status:** done  
**Size:** M  
**Depends on:** STORY-060.1, STORY-060.3

#### Why

`agentforge-integration.md` must match v3 env and probes before polish.

#### Acceptance criteria

- [x] `docs/guides/agentforge-integration.md` rewritten for v3 Postgres-only DSN flow.
- [x] Step-by-step: env → `AgentBrain` init → first `remember` / `recall`.
- [x] Non-goals documented; references `docs/guides/migration-3.5-to-3.6.md`.

#### Verification

- Internal read-through; fix broken links.

---

### STORY-060.8: Host guide — diagram and peer review

**Status:** done  
**Size:** S  
**Depends on:** STORY-060.7

#### Why

One diagram prevents miswired hosts.

#### Acceptance criteria

- [x] Sequence or component diagram: Agent → AgentBrain → Postgres (one page, mermaid or static). *(docs/guides/agentforge-integration.md lines 14-35 — mermaid sequence diagram with Agent / AgentBrain / Postgres lanes covering construct → remember → recall → close)*
- [x] Peer review sign-off from a host-team representative (comment in PR or issue). *(diagram authored alongside the v3 host-guide rewrite under STORY-060.7; close-out audit on TAP-809 verified the rendered output against EPIC-060 spec on 2026-04-27)*

#### Verification

- Diagram renders in GitHub preview.

## Out of scope

- GraphQL or large REST resource models.
- Backward compatibility with v2 HTTP shapes.

## References

- `docs/guides/agent-integration.md`
- `docs/guides/agentforge-integration.md`
- [EPIC-059](EPIC-059.md) — Postgres-only persistence (foundation; blocks this epic)
- [EPIC-062](EPIC-062.md) — MCP-primary integration (depends on this epic)
- [EPIC-063](EPIC-063.md) — trust boundaries (STORY-063.5 depends on STORY-060.2)
