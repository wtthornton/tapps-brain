# ADR-004: Scale — single-node SQLite posture (defer published QPS SLO and service extraction)

**Status:** Superseded by [ADR-007](./ADR-007-postgres-only-no-sqlite.md) (2026-04-11) — Postgres-backed private memory delivered in STORY-059.4–059.6; SQLite is no longer a build or runtime dependency.  
**Date:** 2026-04-03  
**Owner:** @wtthornton  
**Epic / story:** [EPIC-051](../epics/EPIC-051.md) — STORY-051.4  
**Depends on:** [EPIC-050](../epics/EPIC-050.md) STORY-050.2 (**done** — lock ordering / timeout / concurrency doc)  
**Context:** [features-and-technologies.md](../../engineering/features-and-technologies.md) section 10 checklist item 4

## Context

Checklist item 4 asks how far **single-node SQLite** and **`threading.Lock`** can scale, and when to introduce **queues** or **extract a service**.

Documented today:

- [`system-architecture.md`](../../engineering/system-architecture.md) **Concurrency model** — store serialization, lock ordering, optional `TAPPS_STORE_LOCK_TIMEOUT_S`, Postgres connection pool tuning (`TAPPS_BRAIN_PG_POOL_MAX`), operator triage in [`openclaw-runbook.md`](../../guides/openclaw-runbook.md) and [`postgres-backup-restore.md`](../../operations/postgres-backup-runbook.md) *(sqlite-database-locked.md removed — SQLite retired in ADR-007)*.
- Roadmap **MemoryStore modularization** (tracking table row 22) — long-term refactor, design-first only until sustained pain or capacity.

## Decision

1. **Shipped / maintained posture (do):** Treat **one `MemoryStore` per process** on **local SQLite** (plus separate Hive / federation DBs where used) as the **default scale unit**. Prefer **operator tuning** (busy timeout, WAL checkpoint discipline, RO search path, lock timeout for fail-fast) and **workload separation** (multiple store directories or processes) before changing architecture.

2. **Explicitly defer (not committing now):**
   - **Published numeric QPS / SLO envelope** for MCP reads vs writes — **deferred** until a **benchmark harness**, **hardware profile**, and **release process** exist so numbers are evidence-based, not speculative.
   - **Mandatory service extraction** (dedicated write API, read replicas, hosted multi-tenant brain) as part of **core** — **deferred**; would be a **separate** product or deployment mode with its own ADR and boundaries.

3. **Backlog (unchanged intent):** **MemoryStore modularization** remains **backlog** per `open-issues-roadmap.md` — useful to reduce lock hold time **after** profiling shows benefit; not a substitute for multi-node scale-out.

Revisit when **production** shows sustained **`database is locked`**, **lock timeouts**, or **latency SLO breaches** that tuning cannot fix — then re-evaluate **process fan-out**, **read-only replicas**, or **extracted service** in a new decision.

## Consequences

- **No** new required network topology or deployment artifacts from this ADR.
- **Marketing / docs** continue to describe **modest concurrent sessions**, not high-QPS multi-tenant SaaS on a single embedded store.
- **Engineering** may add benchmarks or QPS claims later **without** contradicting this ADR if labeled **environment-specific** and linked to harness revision.

## Scope clarification (ADR-007)

[ADR-007](./ADR-007-postgres-only-no-sqlite.md) narrows this ADR's scope: the single-node SQLite
posture described here applies **only to private agent memory** (`memory.db`). Shared stores
(Hive, Federation) are now Postgres-only. When STORY-059.4–059.6 deliver Postgres-backed private
memory, this ADR will be superseded entirely.

## References

- [`system-architecture.md`](../../engineering/system-architecture.md) — *Concurrency model*, *Scaling posture*.
- [`open-issues-roadmap.md`](../open-issues-roadmap.md) — row 22 (MemoryStore modularization).
- [`EPIC-050.md`](../epics/EPIC-050.md) — concurrency and SQLite discipline stories.
- [`EPIC-051.md`](../epics/EPIC-051.md) — STORY-051.4.
- [`ADR-007.md`](./ADR-007-postgres-only-no-sqlite.md) — Postgres-only Hive and Federation (narrows scope of this ADR).
