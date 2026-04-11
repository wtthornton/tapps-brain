---
id: EPIC-059
title: "Greenfield v3 — Postgres-Only Persistence Plane"
status: in_progress
priority: critical
created: 2026-04-10
updated: 2026-04-11
tags: [greenfield, postgres, persistence, v3, hive, federation]
depends_on: []
blocks: [EPIC-060, EPIC-061]
---

# EPIC-059: Greenfield v3 — Postgres-Only Persistence Plane

## Goal

Ship a v3 persistence layer where **PostgreSQL is the only supported engine** for all durable data (private agent memory, Hive, Federation, registry), with one DSN contract, versioned SQL migrations, and CI/dev workflows that always use real Postgres.

## Motivation

A single shared Hive on Postgres is undermined if clients still default to SQLite Hive or per-machine `memory.db`. One engine yields one backup model, one security review surface, and 2026-realistic ops for agent fleets.

## Context

Pre-GA greenfield: **no migration path from v2**. SQLite backends and file-based `memory.db` paths are removed from the supported product surface so operations, backup, and security have a single engine.

**Stage 2 update (2026-04-11):** the source-level rip-out is complete — `persistence.py`, `sqlite_vec_index.py`, `sqlcipher_util.py`, and the `SqliteAgentRegistryBackend` shim have been deleted. `MemoryStore.__init__` raises `ValueError` when `TAPPS_BRAIN_DATABASE_URL` is unset. Migrations 001–005 cover `private_memories`, the IVFFlat → HNSW upgrade, `feedback_events` + `session_chunks`, `diagnostics_history`, and `audit_log`. ~96% of the unit suite passes against the local Docker Postgres; the remaining ~90 failures are behavioural gaps (consolidation audit trail in merge paths, temporal `as_of` filter on Postgres `search`, archive flow replacement, a few MCP tool-registration issues, and a pre-existing version-consistency test).

## Acceptance Criteria

- [x] No supported runtime path uses SQLite for Hive, Federation, or private agent memory.
- [x] Startup fails with a **clear error** if required Postgres DSN(s) are missing (no silent fallback).
- [x] Schema changes ship as **versioned SQL migrations** for Postgres only.
- [x] `docker compose` (or equivalent) provides a **one-command** local Postgres for developers.
- [ ] CI runs the full test suite against **ephemeral Postgres** (e.g. Testcontainers), not in-memory SQLite. *(Local Docker works; CI workflow update pending.)*

## Stories

### STORY-059.1: Postgres-only factory contracts

**Status:** done  
**Size:** S  
**Depends on:** —

#### Why

Fail-fast DSN validation and typed errors are the foundation; everything else builds on explicit `postgres://` / `postgresql://` only.

#### Acceptance criteria

- [ ] `create_hive_backend` / `create_federation_backend` accept **only** `postgres://` or `postgresql://` DSN strings in the v3 public API.
- [ ] Invalid prefix, empty string, or missing required DSN raises a **documented exception type** (single module or small `exceptions.py` surface).
- [ ] Unit tests: valid DSN accepted; each failure mode has one focused test.

#### Verification

- `pytest` on factory tests only; no integration DB required.

---

### STORY-059.2: Remove SQLite adapter implementations

**Status:** done  
**Size:** M  
**Depends on:** STORY-059.1

#### Why

Shipping `SqliteHiveBackend` / `SqliteFederationBackend` in the package contradicts “one engine.”

#### Acceptance criteria

- [ ] SQLite adapter **classes and modules** removed from the installable package (not feature-flagged).
- [ ] Package `__all__` / public imports updated; dead re-exports removed.
- [ ] Any SQLite-only unit tests deleted or rewritten to Postgres fixtures.

#### Verification

- `grep -r SqliteHiveBackend` / `SqliteFederation` in `src/` returns nothing (except changelog/docs if needed).

---

### STORY-059.3: No silent SQLite in runtime + production docs

**Status:** done  
**Size:** M  
**Depends on:** STORY-059.2

#### Why

Code and docs must agree: no implicit `HiveStore()` / `memory.db`, and no operator doc that reintroduces SQLite as prod.

#### Acceptance criteria

- [ ] MCP server startup: Hive/Federation from env DSN only (exact names per STORY-059.8); no default SQLite path.
- [ ] CLI subcommands that touch Hive/Federation: same contract; documented dev-only escape hatch if any remains (or none).
- [ ] Integration or smoke test: startup fails clearly when DSN missing in strict mode.
- [ ] `docs/engineering/` and operator guides: **no** SQLite as a supported production path for Hive/Federation/private memory; ADR-007 linked from architecture overview.

#### Verification

- Grep + Testcontainers smoke + doc review checklist.

---

### STORY-059.4: Private memory — schema and migrations

**Status:** done  
**Size:** L  
**Depends on:** STORY-059.2

#### Why

Tenant columns and forward-only SQL migrations must exist before wiring `MemoryStore` to Postgres.

#### Acceptance criteria

- [ ] Postgres tables for private agent memory keyed by `project_id` (or canonical repo hash) and `agent_id`.
- [ ] Versioned `migrations/*.sql` (forward-only; no SQLite branches).
- [ ] Migration applies cleanly on empty DB; revision id recorded.

#### Verification

- Migration tests against disposable Postgres.

---

### STORY-059.5: Private memory — indexes and store wiring

**Status:** done  
**Size:** L  
**Depends on:** STORY-059.4

#### Why

Hot paths are recall / search; indexes and write-through behavior must match product SLOs.

#### Acceptance criteria

- [ ] Indexes suitable for BM25-adjacent / hot recall queries (documented in migration comments).
- [ ] Default v3 layout **does not** create `.tapps-brain/agents/<id>/memory.db`.
- [ ] `MemoryStore` (or successor) reads/writes private rows through Postgres backend; feature-flag or phase if incremental.

#### Verification

- Integration tests: round-trip save/recall with N entries.

---

### STORY-059.6: Behavioral parity and load smoke

**Status:** in_progress  
**Size:** M  
**Depends on:** STORY-059.5

#### Why

Greenfield allows breaking changes, but intentional deltas must be documented and performance bounded.

#### Acceptance criteria

- [ ] Short **parity doc**: decay, consolidation, safety — what matches v2 vs what changed (links to ADR/epic).
- [ ] Benchmark or load smoke: **N** concurrent agents (define N in PR) against one Postgres; record p95 latency budget or “informational only” if pre-SLO.

#### Verification

- CI optional job or local script documented in `AGENTS.md`.

---

### STORY-059.7: DSN table, pool tuning, and health fields

**Status:** done  
**Size:** M  
**Depends on:** STORY-059.1

#### Why

One table for operators; pool and migration visibility prevent silent overload.

#### Acceptance criteria

- [ ] Single **README / env table**: `TAPPS_BRAIN_DATABASE_URL` **or** split `HIVE` / `FEDERATION` DSNs — one coherent story, minimal variables.
- [ ] Pool: max connections, idle timeout, connect timeout — env-configurable with documented defaults.
- [ ] Health / readiness JSON includes **pool saturation** (or queue depth) and **last applied migration version**.
- [ ] Malformed URL: fail at config parse with clear error (unit test).

#### Verification

- Unit tests for parsing; integration test for health JSON against Postgres.

---

### STORY-059.8: Compose, Makefile, CI, and onboarding

**Status:** in_progress  
**Size:** L  
**Depends on:** STORY-059.4, STORY-059.7

#### Why

Postgres-only fails adoption if local and CI are harder than today; one story ties compose, automation, and green CI.

#### Acceptance criteria

- [ ] Repo-root `docker-compose` (or profile) starts Postgres **+** pgvector image aligned with prod.
- [ ] `Makefile` or `justfile` targets: e.g. `brain-up`, `brain-down`, `brain-test`; documented in `AGENTS.md` / `README` with copy-paste local DSN.
- [ ] CI test job uses **ephemeral Postgres** (service container or Testcontainers); no flaky shared DB.
- [ ] PR template or contributing snippet: “clone → compose → pytest” with **target minutes** (e.g. ≤ 15); optional minimal seed script for demos.

#### Verification

- Green CI; maintainer dry-run recorded in epic PR description.

## Out of scope

- Migrating existing v2 SQLite user data.
- Offline/air-gapped use without any Postgres instance.

## References

- `docs/engineering/system-architecture.md`
- `docs/guides/hive-deployment.md`
- `docs/planning/adr/ADR-007-postgres-only-no-sqlite.md` (decision record for this epic)
- [EPIC-060](EPIC-060.md) — agent-first API (blocked by this epic)
- [EPIC-061](EPIC-061.md) — observability (blocked by this epic)
- [EPIC-062](EPIC-062.md) — MCP-primary integration (blocked by this epic)
- [EPIC-063](EPIC-063.md) — trust boundaries (blocked by this epic)
