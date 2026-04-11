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

### STORY-060.1: Agent integration page — API surface

**Status:** planned  
**Size:** S  
**Depends on:** EPIC-059 foundation (types stable)

#### Why

Hosts need one page listing public methods and env vars before exception taxonomy.

#### Acceptance criteria

- [ ] New or refreshed **Agent integration** page lists public `AgentBrain` (or successor) methods: `remember`, `recall`, `forget`, `learn_from_success`, `learn_from_failure` (or v3 names).
- [ ] Env vars on one table: `TAPPS_BRAIN_AGENT_ID`, `TAPPS_BRAIN_PROJECT_DIR`, group/expert CSVs, DSN variable names (link EPIC-059 env table).
- [ ] Cross-links from `README` / `AGENTS.md`.

#### Verification

- Doc PR review; link check.

---

### STORY-060.2: Agent integration page — exceptions and breaking changes

**Status:** planned  
**Size:** S  
**Depends on:** STORY-060.1

#### Why

Typed errors and explicit v3 breaks reduce support load.

#### Acceptance criteria

- [ ] Documented exception types: configuration vs transient DB vs validation (map to actual classes in code).
- [ ] Short **v3 breaking changes** subsection: renames allowed; no compatibility shim required (greenfield).
- [ ] Optional: mypy-public re-export list or `api` module snapshot.

#### Verification

- Doc review + spot-check against `agent_brain.py`.

---

### STORY-060.3: HTTP adapter — liveness, readiness, metrics

**Status:** planned  
**Size:** M  
**Depends on:** STORY-060.2

#### Why

Smallest useful HTTP surface: orchestrators need probes before optional hooks.

#### Acceptance criteria

- [ ] **`/health`**: process up (cheap; no DB required).
- [ ] **`/ready`**: DB ping + migration version (or degraded JSON); aligns with EPIC-061 semantics.
- [ ] **`/metrics`**: Prometheus text **or** OTel-native per EPIC-061 (pick one in PR; document).

#### Verification

- Contract tests: HTTP status codes for healthy vs DB down (mocked).

---

### STORY-060.4: HTTP adapter — optional routes, auth, OpenAPI

**Status:** planned  
**Size:** M  
**Depends on:** STORY-060.3

#### Why

At most two extra routes for host hooks; auth documented as mandatory in prod.

#### Acceptance criteria

- [ ] At most **two** additional routes (e.g. single `POST` orchestration hook); each behind optional auth middleware.
- [ ] README/ADR: **auth required in production** for any non-probe route.
- [ ] OpenAPI spec **≤ one printed page**; no CRUD for memory keys.

#### Verification

- OpenAPI snapshot test; fuzz 401/403 on protected routes.

---

### STORY-060.5: ADR — no new HTTP without MCP + library parity

**Status:** planned  
**Size:** S  
**Depends on:** STORY-060.4

#### Why

Prevents endpoint creep across future PRs.

#### Acceptance criteria

- [ ] ADR committed: no new public HTTP routes without **library + MCP** parity for the same capability.
- [ ] Linked from `docs/engineering/` index or architecture README.

#### Verification

- Maintainer approval on ADR PR.

---

### STORY-060.6: Guardrails — CI or CODEOWNERS for HTTP tree

**Status:** planned  
**Size:** S  
**Depends on:** STORY-060.5

#### Why

Automation backs the ADR when humans forget.

#### Acceptance criteria

- [ ] Optional: `CODEOWNERS` entry for `**/http/**/*.py` or agreed path; **or** CI script that fails if new route file lacks ADR reference in PR template.
- [ ] Documented in contributing or release checklist.

#### Verification

- Dry-run PR that touches HTTP path triggers review rule.

---

### STORY-060.7: Host guide — Postgres-only rewrite (body)

**Status:** planned  
**Size:** M  
**Depends on:** STORY-060.1, STORY-060.3

#### Why

`agentforge-integration.md` must match v3 env and probes before polish.

#### Acceptance criteria

- [ ] `docs/guides/agentforge-integration.md` (or v3 successor): **Postgres-only** DSN flow; no SQLite Hive.
- [ ] Step-by-step: env → `AgentBrain` init → first `remember` / `recall`.
- [ ] Explicit **non-goals**: duplicating full MCP tool surface over HTTP.

#### Verification

- Internal read-through; fix broken links.

---

### STORY-060.8: Host guide — diagram and peer review

**Status:** planned  
**Size:** S  
**Depends on:** STORY-060.7

#### Why

One diagram prevents miswired hosts.

#### Acceptance criteria

- [ ] Sequence or component diagram: Agent → AgentBrain → Postgres (one page, mermaid or static).
- [ ] Peer review sign-off from a host-team representative (comment in PR or issue).

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
