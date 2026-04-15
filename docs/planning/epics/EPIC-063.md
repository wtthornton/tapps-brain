---
id: EPIC-063
title: "Greenfield v3 — Trust Boundaries & Postgres Enforcement"
status: done
priority: high
created: 2026-04-10
completed: 2026-04-15
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

- [x] ADR committed: **when RLS** vs **app-layer only** for multi-project isolation. *(ADR-009)*
- [x] **Migrator** DB role separate from **runtime** role. *(migrations/roles/001_db_roles.sql)*
- [x] Threat model one-pager published. *(docs/engineering/threat-model.md)*
- [x] Security review checklist for any new public endpoint. *(scope-audit.md + ADR-008)*

## Stories

### STORY-063.1: DB roles — migration SQL

**Status:** done  
**Size:** M  
**Depends on:** EPIC-059 STORY-059.8 (CI/dev Postgres + migrations runnable)

#### Why

Roles must exist in schema before apps connect as least privilege.

#### Acceptance criteria

- [x] Migration creates roles: `tapps_runtime` (scoped DML), `tapps_migrator` (DDL), optional `tapps_readonly`. *(migrations/roles/001_db_roles.sql)*
- [x] `GRANT`/`REVOKE` statements idempotent where possible; documented in migration folder README.

#### Verification

- Apply migration on fresh DB; `\du` or query role list.

---

### STORY-063.2: DB roles — runbooks and DSN hygiene

**Status:** done  
**Size:** S  
**Depends on:** STORY-063.1

#### Why

Operators must use runtime DSN only; migrator used only in deploy jobs.

#### Acceptance criteria

- [x] Runbook snippet: prod uses **runtime** DSN; CI uses migrator only for `migrate` job. *(docs/guides/postgres-dsn.md)*
- [x] Documented: no DSN plaintext in logs; load from secret store / env injection.
- [x] Application reads DSN from env or secret reference.

#### Verification

- Grep audit for accidental DSN logging in touched code.

---

### STORY-063.3: RLS spike — policy on one table

**Status:** done  
**Size:** M  
**Depends on:** STORY-063.1, EPIC-059 STORY-059.4 (tenant key columns in schema)

#### Why

Prove session `SET` + policy syntax before GA decision.

#### Acceptance criteria

- [x] One table (hive_memories): `ENABLE ROW LEVEL SECURITY`; policy on `project_id`. *(migrations/hive/002_rls_spike.sql)*
- [x] Connection opener sets session vars: `SET LOCAL app.project_id`. *(postgres_private.py)*

#### Verification

- Integration test: two projects cannot read each other’s rows when RLS enabled.

---

### STORY-063.4: RLS spike — performance and ship/defer decision

**Status:** done  
**Size:** M  
**Depends on:** STORY-063.3

#### Why

RLS overhead must be measured before GA commitment.

#### Acceptance criteria

- [x] Benchmark before/after on representative query mix; document % overhead. *(ADR-009: 3–9% overhead, below 15% threshold)*
- [x] ADR update: **ship RLS in GA** — accepted. *(ADR-009 decision: SHIP)*
- [x] Defence-in-depth: app-layer + DB roles + RLS all documented.

#### Verification

- Benchmark script or CI job artifact attached to PR.

---

### STORY-063.5: Scope audit — matrix doc

**Status:** done  
**Size:** S  
**Depends on:** EPIC-060 STORY-060.2 (exceptions + contract doc stable)

#### Why

Audit needs a written scope → namespace map.

#### Acceptance criteria

- [x] Matrix: `agent_scope` / group / hive → allowed namespaces and operations. *(docs/guides/scope-audit.md)*
- [x] Published under `docs/guides/` linked from hive.md.

#### Verification

- Review with Hive maintainer.

---

### STORY-063.6: Scope audit — code checklist and gap filing

**Status:** done  
**Size:** M  
**Depends on:** STORY-063.5

#### Why

Matrix is useless without traceability to code paths.

#### Acceptance criteria

- [x] Checklist table: path → scope rule → reviewed. *(docs/guides/scope-audit.md — STORY-063.5/063.6)*
- [x] Gaps filed or explicitly noted as “no gaps.”

#### Verification

- PR checklist complete or explicit “no gaps” statement.

---

### STORY-063.7: Scope audit — negative tests

**Status:** done  
**Size:** M  
**Depends on:** STORY-063.6

#### Why

Regression tests enforce the matrix.

#### Acceptance criteria

- [x] Tests: wrong `agent_id` cannot write cross-tenant row. *(tests/integration/test_rls_spike.py, test_tenant_isolation.py, test_per_tenant_auth_isolation.py)*
- [x] Tests: cross-project isolation verified end-to-end.
- [x] CI green on integration suite.

#### Verification

- CI green; review comments on PR.

---

### STORY-063.8: Threat model — STRIDE one-pager

**Status:** done  
**Size:** S  
**Depends on:** —

#### Why

Stakeholders (TheStudio, AgentForge) need shared vocabulary for risk.

#### Acceptance criteria

- [x] STRIDE bullets with mitigation references. *(docs/engineering/threat-model.md)*
- [x] Explicit out of scope for v3.0.

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
