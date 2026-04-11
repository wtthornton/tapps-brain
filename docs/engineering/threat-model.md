---
title: "tapps-brain v3 Threat Model (STRIDE)"
version: "3.0"
created: 2026-04-11
status: current
tags: [security, threat-model, stride, v3]
---

# tapps-brain v3 Threat Model

**Scope:** tapps-brain v3.0 — Postgres-only persistence plane with private agent memory,
Hive (cross-agent shared memory), Federation (cross-project memory), MCP server, and HTTP
adapter (EPIC-059 through EPIC-063). This model uses **STRIDE** to identify threats and
link mitigations to existing controls.

## System Context

```
  Claude Code / Agent SDK
         │ (AgentBrain API / MCP tools)
         ▼
  tapps-brain process
  ├── AgentBrain facade (agent_brain.py)
  ├── MemoryStore (store.py) ─────────────► Private Postgres DB
  ├── PropagationEngine (hive)             (project_id + agent_id keyed)
  └── HTTP adapter (http_adapter.py)
         │
         ▼
  Shared Postgres DB (Hive / Federation)
  (project_id + org_id isolated; optional RLS)
```

---

## STRIDE Threat Table

### S — Spoofing

| Threat | Scenario | Mitigation | Reference |
|--------|----------|------------|-----------|
| Agent identity spoofing | A malicious agent passes a fabricated `agent_id`, reads or writes another agent's private memories. | `agent_id` is enforced as a session-scoped binding at the store layer; DB role `tapps_runtime` has DML only on rows matching the caller's `project_id`. RLS (EPIC-063 STORY-063.3) adds a second enforcement layer. | [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md), STORY-063.1 |
| DSN / credential theft | Stolen Postgres DSN grants full DB access. | Least-privilege roles: `tapps_runtime` (DML, no DDL), `tapps_migrator` (DDL only in deploy jobs). No DSN logged in plaintext. Load via env injection or secret store. | STORY-063.1, STORY-063.2, [postgres-dsn.md](../guides/postgres-dsn.md) |
| MCP caller impersonation | A process connects to the MCP server without a verified identity. | MCP server runs in-process with the agent SDK; not exposed as a public network service. HTTP adapter auth middleware (EPIC-060 STORY-060.4) guards any HTTP routes that expose memory operations. | [ADR-008](../planning/adr/ADR-008-no-http-without-mcp-library-parity.md), STORY-060.4 |

---

### T — Tampering

| Threat | Scenario | Mitigation | Reference |
|--------|----------|------------|-----------|
| Memory poisoning via RAG injection | Adversarial content stored in memory that, when recalled, manipulates agent behavior. | `safety.py` detects and blocks prompt-injection patterns at ingest and recall. Safety checks run before any entry enters the store. | [safety.py](../../src/tapps_brain/safety.py) |
| Cross-agent write (Hive) | Agent A writes to Hive with `agent_id` of Agent B, corrupting B's view. | Propagation engine enforces `agent_scope` rules. Scope audit matrix (STORY-063.5) documents allowed operations per scope. Negative tests (STORY-063.7) verify rejection. | [hive.md](../guides/hive.md), STORY-063.5 |
| Schema tampering by runtime role | `tapps_runtime` executes DDL, alters tables, or drops data. | `tapps_runtime` granted DML only (`INSERT`, `UPDATE`, `DELETE`, `SELECT`); DDL reserved for `tapps_migrator` which is only used in migration CI jobs. | STORY-063.1 |

---

### R — Repudiation

| Threat | Scenario | Mitigation | Reference |
|--------|----------|------------|-----------|
| Undetected memory writes | Agent denies writing a memory entry; no audit trail. | JSONL audit log (`memory_log.jsonl`) records every mutation with timestamp, `agent_id`, and operation type. Postgres write-through ensures log consistency. | [store.py](../../src/tapps_brain/store.py) |
| Hive propagation without attribution | Entry propagated to Hive with no source record. | `MemoryEntry.agent_id` and `source` fields are required and persisted; Hive backend stores them as non-nullable columns. | [models.py](../../src/tapps_brain/models.py) |

---

### I — Information Disclosure

| Threat | Scenario | Mitigation | Reference |
|--------|----------|------------|-----------|
| Cross-project memory read | Project A reads Hive entries belonging to Project B. | Schema columns `project_id` / `org_id` on all shared tables. App-layer filter on every query. Optional RLS policy (EPIC-063 STORY-063.3) enforces isolation at the DB layer. | STORY-063.3, [data-stores-and-schema.md](data-stores-and-schema.md) |
| Telemetry leaks memory content | OTel spans or metrics export raw memory bodies or query strings. | Telemetry policy forbids memory body, query text, and PII in span attributes or metric labels. Log formatter strips/hashes memory bodies. | [telemetry-policy.md](../operations/telemetry-policy.md), STORY-061.7 |
| DSN in logs | Postgres DSN (including password) written to application logs or OTel traces. | `postgres_connection.py` never logs the full DSN; sanitizes to `postgres://<host>/<db>` in debug output. Policy documented for operators. | STORY-063.2 |

---

### D — Denial of Service

| Threat | Scenario | Mitigation | Reference |
|--------|----------|------------|-----------|
| Memory store exhaustion | Unchecked writes fill private DB or Hive, starving legitimate agents. | `MemoryStore` enforces a per-project cap (default 5,000 entries); profile-configurable `limits.max_entries_per_group`. Eviction on cap breach. | [profiles.md](../guides/profiles.md), STORY-044.7 |
| Connection pool exhaustion | Many agents flood the Postgres connection pool, blocking all memory operations. | `PostgresConnectionManager` uses bounded `psycopg_pool`; pool size, idle timeout, and max-wait configurable via env vars (`TAPPS_BRAIN_PG_MAX_CONN`, etc.). `/ready` probe exposes pool saturation. | [postgres_connection.py](../../src/tapps_brain/postgres_connection.py), STORY-059.7 |
| Consolidation storm | Rapid writes trigger repeated consolidation, consuming CPU. | `auto_consolidation.py` uses rate limiting and deferred scheduling. Consolidation is deterministic and bounded by similarity thresholds. | [auto_consolidation.py](../../src/tapps_brain/auto_consolidation.py) |

---

### E — Elevation of Privilege

| Threat | Scenario | Mitigation | Reference |
|--------|----------|------------|-----------|
| Runtime role executes migrations | `tapps_runtime` DSN used to run DDL migrations, unintentionally altering schema. | Roles are separate (`tapps_runtime` vs `tapps_migrator`). Migration entry points check role capability and document the correct DSN for each job type. | STORY-063.1, STORY-063.2 |
| MCP operator tools exposed to all agents | Maintenance tools (GC, consolidation-undo, diagnostics reset) run by any MCP caller. | Operator tools gated behind `--enable-operator-tools` flag or profile setting (EPIC-062 STORY-062.4); not exposed in default sessions. | STORY-062.4 |
| HTTP endpoint without auth bypasses scope | Unauthenticated HTTP call writes memory or reads cross-project data. | HTTP adapter requires auth middleware on all write and read routes (EPIC-060 STORY-060.4). ADR-008 mandates MCP + library parity before any HTTP route is added. | [ADR-008](../planning/adr/ADR-008-no-http-without-mcp-library-parity.md), STORY-060.4 |

---

## Explicit Out of Scope for v3.0

The following threats are **acknowledged but not addressed** in v3.0. They require product
decisions or infrastructure outside this codebase:

| Out-of-Scope Item | Rationale |
|-------------------|-----------|
| Multi-tenant SaaS isolation (hard tenant boundaries, per-org encryption keys) | v3.0 targets single-org or trusted-multi-agent deployments. Multi-tenant SaaS requires customer-managed KMS and stricter RLS. Revisit if product expands. |
| HSM-backed credential storage | Operator responsibility via secret store (Vault, AWS Secrets Manager, etc.). tapps-brain only consumes DSN from env. |
| Network-level TLS enforcement | Postgres TLS settings are operator-configured (Postgres `ssl=require` / `sslmode`). tapps-brain passes the DSN as-is. |
| Zero-trust mesh / mTLS between agents | Agent SDK / orchestration layer responsibility, not tapps-brain. |
| Key rotation automation | Out of scope for v3.0; documented in `docs/guides/postgres-dsn.md` as operator procedure. |
| DoS from external internet (rate limiting, WAF) | HTTP adapter is not expected to face the public internet without an API gateway. |

---

## Security Review Checklist (New Public Endpoints)

For any **new HTTP route** or **new MCP tool**, the PR author must confirm:

- [ ] Scope rules applied: `agent_id` / `project_id` enforced at store layer, not just route layer.
- [ ] No raw memory bodies in logs, OTel spans, or error messages (reference [telemetry-policy.md](../operations/telemetry-policy.md)).
- [ ] Auth middleware applied (HTTP) or tool gated behind operator flag (MCP operator tools).
- [ ] No plaintext DSN or secret in logs or response bodies.
- [ ] Negative test: unauthorized caller receives expected rejection (4xx / typed exception).
- [ ] ADR-008 parity check: if adding HTTP route, ensure MCP tool exists with equivalent capability.

---

## References

- [ADR-007: Postgres-only, no SQLite](../planning/adr/ADR-007-postgres-only-no-sqlite.md)
- [ADR-008: No HTTP without MCP + library parity](../planning/adr/ADR-008-no-http-without-mcp-library-parity.md)
- [EPIC-059: Greenfield v3 — Postgres-Only Persistence Plane](../planning/epics/EPIC-059.md)
- [EPIC-060: Agent-First Core & Minimal Runtime API](../planning/epics/EPIC-060.md)
- [EPIC-063: Trust Boundaries & Postgres Enforcement](../planning/epics/EPIC-063.md)
- [hive.md — scopes and namespaces](../guides/hive.md)
- [telemetry-policy.md — allowed vs forbidden telemetry](../operations/telemetry-policy.md)
- [postgres-dsn.md — DSN configuration](../guides/postgres-dsn.md)
- [data-stores-and-schema.md — schema reference](data-stores-and-schema.md)
