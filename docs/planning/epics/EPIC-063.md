---
id: EPIC-063
title: "Greenfield v3 — Trust Boundaries & Postgres Enforcement"
status: planned
priority: high
created: 2026-04-10
tags: [greenfield, security, postgres, rls, trust, v3]
depends_on: [EPIC-059]
blocks: []
---

# EPIC-063: Greenfield v3 — Trust Boundaries & Postgres Enforcement

## Goal

Enforce **least-privilege Postgres roles**, document an **RLS vs app-layer** decision, and publish a **threat model** so multi-project deployments do not rely on client honesty alone.

## Motivation

A single DSN with superuser-like powers is a high-value target; 2026 deployments expect defense in depth and auditable scope checks on shared memory.

## Context

Postgres-only **increases** the value of stolen credentials. Defense in depth: **least-privilege DB roles**, optional **RLS**, and **application-scope checks** on every write. Greenfield allows schema columns (`org_id`, `project_id`) intended for policy from day one—no retrofit-only story later.

## Acceptance Criteria

- [ ] ADR committed: **when RLS** vs **app-layer only** for multi-project isolation.
- [ ] **Migrator** DB role separate from **runtime** role (migrations cannot run as superuser in prod).
- [ ] Threat model one-pager published (memory poisoning, cross-project read, credential theft).
- [ ] Security review checklist for any new public endpoint (HTTP or MCP).

## Stories

### STORY-063.1: Postgres roles & least privilege

**Status:** planned  
**Size:** M  
**Depends on:** STORY-059.4 (CI/dev Postgres in place; migrations running)

#### Why

One superuser DSN for everything is convenient and dangerous.

#### Acceptance criteria

- [ ] Migration creates roles: e.g. `tapps_runtime` (SELECT/INSERT/UPDATE scoped), `tapps_migrator` (DDL), optional `tapps_readonly` for analytics.
- [ ] `GRANT` documented in migration README; prod runbooks use **runtime** only.
- [ ] Application supports reading DSN from secret store; **documented** no plaintext in logs.

#### Verification

- CI applies migrations with migrator; app tests use runtime role only.

---

### STORY-063.2: RLS policy spike (optional for GA)

**Status:** planned  
**Size:** L  
**Depends on:** STORY-063.1, STORY-059.2 (Postgres schema with `project_id`/`agent_id` tenant keys)

#### Why

For multi-project servers, DB-enforced row scope catches app bugs.

#### Acceptance criteria

- [ ] Spike on **one** table (e.g. hive entries): policy by `project_id` or `org_id` using session `SET` from connection opener.
- [ ] Documented **performance** impact (single-digit % overhead target or measured).
- [ ] Decision: **ship RLS in GA** vs **defer** with explicit risk acceptance.

#### Verification

- Benchmark before/after; security review notes.

---

### STORY-063.3: Scope validation audit

**Status:** planned  
**Size:** M  
**Depends on:** STORY-060.1 (AgentBrain contract freeze — types and scope semantics stable)

#### Why

Every `remember` / propagate path must enforce agent identity and `agent_scope`.

#### Acceptance criteria

- [ ] Matrix documented: scope → allowed namespaces / groups.
- [ ] Code audit checklist completed; gaps filed as blockers or follow-ups.
- [ ] Regression tests for negative cases (wrong agent, wrong group).

#### Verification

- Peer review sign-off from two maintainers.

---

### STORY-063.4: Threat model one-pager

**Status:** planned  
**Size:** S  
**Depends on:** —

#### Why

Stakeholders (TheStudio, AgentForge) need shared vocabulary for risk.

#### Acceptance criteria

- [ ] STRIDE-style bullets: spoofing, tampering, repudiation, information disclosure, DoS, elevation—mapped to mitigations.
- [ ] Explicit **out of scope** for v3.0 (e.g. “no multi-tenant SaaS isolation guarantee” if single-org only).

#### Verification

- Review with platform owner.

## Out of scope

- Full zero-trust mesh, HSM-backed keys, or customer-managed KMS (unless product expands).

## References

- `docs/guides/hive.md` (scopes, namespaces)
- `adr/` (new ADR for RLS decision)
- [EPIC-059](EPIC-059.md) — Postgres-only persistence (foundation; blocks this epic)
- [EPIC-060](EPIC-060.md) — agent-first API (STORY-063.3 depends on STORY-060.1)
- [EPIC-062](EPIC-062.md) — MCP-primary integration (shares public endpoint auth surface)
