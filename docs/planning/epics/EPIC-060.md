---
id: EPIC-060
title: "Greenfield v3 — Agent-First Core & Minimal Runtime API"
status: planned
priority: critical
created: 2026-04-10
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

- [ ] **Agent-first** Python API is the primary integration path in all public docs.
- [ ] If an HTTP server ships with the product, it exposes **≤ 10 documented routes** (including health/metrics), with each route justified in an ADR or inline table.
- [ ] No memory feature ships **HTTP-only** without **MCP + library** parity for agents.
- [ ] Embeddable hosts (e.g. AgentForge) integrate via **one** documented adapter pattern.

## Stories

### STORY-060.1: AgentBrain contract freeze (v3)

**Status:** planned  
**Size:** M  
**Depends on:** EPIC-059 foundation (types stable)

#### Why

Agents and hosts need a stable, minimal contract without leaking storage details.

#### Acceptance criteria

- [ ] Public methods, env vars (`TAPPS_BRAIN_AGENT_ID`, `TAPPS_BRAIN_PROJECT_DIR`, group/expert CSVs, DSNs) documented in a **single** “Agent integration” page.
- [ ] Exceptions are typed and documented (config vs transient DB vs validation).
- [ ] Breaking renames from v2 are allowed; no compatibility shim required.

#### Verification

- Doc review + mypy-public API check as applicable.

---

### STORY-060.2: Minimal HTTP host adapter (optional package)

**Status:** planned  
**Size:** L  
**Depends on:** STORY-060.1

#### Why

Some deployments want a process boundary; the surface must stay minimal.

#### Acceptance criteria

- [ ] Implements: **`/health`** (liveness), **`/ready`** (DB + migrations), **`/metrics`** (Prometheus text or OTel-native per EPIC-061).
- [ ] At most **two** additional routes for host use cases (e.g. single `POST` hook for orchestration), each behind optional auth middleware **documented as required in production**.
- [ ] OpenAPI spec **≤ 1 page** total; no CRUD mirror of memory keys.

#### Verification

- Contract test against OpenAPI; fuzz auth rejection.

---

### STORY-060.3: Anti-pattern guardrails

**Status:** planned  
**Size:** S  
**Depends on:** STORY-060.2

#### Why

Prevents endpoint creep in future PRs.

#### Acceptance criteria

- [ ] ADR: “No new public HTTP routes without library + MCP parity.”
- [ ] Optional: CI script or CODEOWNERS rule for `**/http/**/*.py` (paths TBD).

#### Verification

- Maintainer sign-off on ADR merge.

---

### STORY-060.4: Host integration guide (AgentForge / TheStudio)

**Status:** planned  
**Size:** M  
**Depends on:** STORY-060.1–060.2

#### Why

Hosts must wire identity and DSN once; docs replace tribal knowledge.

#### Acceptance criteria

- [ ] `docs/guides/agentforge-integration.md` (or v3 successor) rewritten for **Postgres-only + agent-first** flow.
- [ ] Sequence diagram: Agent → AgentBrain → Postgres (one page).
- [ ] Explicit **non-goals**: duplicating full MCP tool surface over HTTP.

#### Verification

- Peer review from a host team representative.

## Out of scope

- GraphQL or large REST resource models.
- Backward compatibility with v2 HTTP shapes.

## References

- `docs/guides/agent-integration.md`
- `docs/guides/agentforge-integration.md`
- [EPIC-059](EPIC-059.md) — Postgres-only persistence (foundation; blocks this epic)
- [EPIC-062](EPIC-062.md) — MCP-primary integration (depends on this epic)
- [EPIC-063](EPIC-063.md) — trust boundaries (STORY-063.3 depends on this epic)
