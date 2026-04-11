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

### STORY-063.1: DB roles — migration SQL

**Status:** planned  
**Size:** M  
**Depends on:** EPIC-059 STORY-059.8 (CI/dev Postgres + migrations runnable)

#### Why

Roles must exist in schema before apps connect as least privilege.

#### Acceptance criteria

- [ ] Migration creates roles: `tapps_runtime` (scoped DML), `tapps_migrator` (DDL), optional `tapps_readonly`.
- [ ] `GRANT`/`REVOKE` statements idempotent where possible; documented in migration folder README.

#### Verification

- Apply migration on fresh DB; `\du` or query role list.

---

### STORY-063.2: DB roles — runbooks and DSN hygiene

**Status:** planned  
**Size:** S  
**Depends on:** STORY-063.1

#### Why

Operators must use runtime DSN only; migrator used only in deploy jobs.

#### Acceptance criteria

- [ ] Runbook snippet: prod uses **runtime** DSN; CI uses migrator only for `migrate` job.
- [ ] Documented: no DSN plaintext in logs; load from secret store / env injection.
- [ ] Application reads DSN from env or secret reference (no new logging of full URL).

#### Verification

- Grep audit for accidental DSN logging in touched code.

---

### STORY-063.3: RLS spike — policy on one table

**Status:** planned  
**Size:** M  
**Depends on:** STORY-063.1, EPIC-059 STORY-059.4 (tenant key columns in schema)

#### Why

Prove session `SET` + policy syntax before GA decision.

#### Acceptance criteria

- [ ] One table (e.g. hive entries): `ENABLE ROW LEVEL SECURITY`; policy on `project_id` or `org_id`.
- [ ] Connection opener sets session vars consumed by policy (documented pattern).

#### Verification

- Integration test: two projects cannot read each other’s rows when RLS enabled.

---

### STORY-063.4: RLS spike — performance and ship/defer decision

**Status:** planned  
**Size:** M  
**Depends on:** STORY-063.3

#### Why

RLS overhead must be measured before GA commitment.

#### Acceptance criteria

- [ ] Benchmark before/after on representative query mix; document **% overhead**.
- [ ] ADR update: **ship RLS in GA** vs **defer** with explicit risk acceptance.
- [ ] If defer: document compensating app-layer controls (link STORY-063.5–063.6).

#### Verification

- Benchmark script or CI job artifact attached to PR.

---

### STORY-063.5: Scope audit — matrix doc

**Status:** planned  
**Size:** S  
**Depends on:** EPIC-060 STORY-060.2 (exceptions + contract doc stable)

#### Why

Audit needs a written scope → namespace map.

#### Acceptance criteria

- [ ] Matrix: `agent_scope` / group / hive → allowed namespaces and operations.
- [ ] Published under `docs/guides/` or engineering (linked from hive.md).

#### Verification

- Review with Hive maintainer.

---

### STORY-063.6: Scope audit — code checklist and gap filing

**Status:** planned  
**Size:** M  
**Depends on:** STORY-063.5

#### Why

Matrix is useless without traceability to code paths.

#### Acceptance criteria

- [ ] Checklist table: path (module/function) → scope rule → reviewed by (initials/date).
- [ ] Gaps filed as GitHub issues with `security` label or epic follow-up.

#### Verification

- PR checklist complete or explicit “no gaps” statement.

---

### STORY-063.7: Scope audit — negative tests

**Status:** planned  
**Size:** M  
**Depends on:** STORY-063.6

#### Why

Regression tests enforce the matrix.

#### Acceptance criteria

- [ ] Tests: wrong `agent_id` cannot write cross-tenant row (where applicable).
- [ ] Tests: wrong group membership rejected on propagate (expected error type).
- [ ] Peer review: two maintainers sign off on test list vs matrix.

#### Verification

- CI green; review comments on PR.

---

### STORY-063.8: Threat model — STRIDE one-pager

**Status:** planned  
**Size:** S  
**Depends on:** —

#### Why

Stakeholders (TheStudio, AgentForge) need shared vocabulary for risk.

#### Acceptance criteria

- [ ] STRIDE bullets: spoofing, tampering, repudiation, information disclosure, DoS, elevation — each with mitigation reference (doc link or ADR).
- [ ] Explicit **out of scope** for v3.0 (e.g. no multi-tenant SaaS guarantee if single-org only).

#### Verification

- Review with platform owner (comment or meeting note in PR).

## Out of scope

- Full zero-trust mesh, HSM-backed keys, or customer-managed KMS (unless product expands).

## References

- `docs/guides/hive.md` (scopes, namespaces)
- `adr/` (new ADR for RLS decision)
- [EPIC-059](EPIC-059.md) — Postgres-only persistence (foundation; blocks this epic)
- [EPIC-060](EPIC-060.md) — agent-first API (STORY-063.5 depends on STORY-060.2)
- [EPIC-062](EPIC-062.md) — MCP-primary integration (shares public endpoint auth surface)
