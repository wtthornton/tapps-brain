---
id: EPIC-059
title: "Greenfield v3 — Postgres-Only Persistence Plane"
status: planned
priority: critical
created: 2026-04-10
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

## Acceptance Criteria

- [ ] No supported runtime path uses SQLite for Hive, Federation, or private agent memory.
- [ ] Startup fails with a **clear error** if required Postgres DSN(s) are missing (no silent fallback).
- [ ] Schema changes ship as **versioned SQL migrations** for Postgres only.
- [ ] CI runs the full test suite against **ephemeral Postgres** (e.g. Testcontainers), not in-memory SQLite.
- [ ] `docker compose` (or equivalent) provides a **one-command** local Postgres for developers.

## Stories

### STORY-059.1: Remove SQLite backends from the supported surface

**Status:** planned  
**Size:** L  
**Depends on:** —

#### Why

Dual backends (`SqliteHiveBackend`, `SqliteFederationBackend`, file `HiveStore`) defeat “one Hive” and duplicate test matrices.

#### Acceptance criteria

- [ ] `create_hive_backend` / `create_federation_backend` accept **only** `postgres://` or `postgresql://` DSNs in the v3 API; invalid or missing DSN fails fast with a documented exception type.
- [ ] SQLite adapter modules are **deleted** from the installable package (not deprecated behind flags—greenfield).
- [ ] No default `HiveStore()` SQLite construction in MCP, CLI, or health checks without an explicit **non-production** dev-only path (prefer: always Postgres; see STORY-059.4 for CI/dev).
- [ ] Engineering docs list **zero** SQLite paths for production deployments.

#### Verification

- Unit + integration tests run **Postgres only**; SQLite-specific tests removed or rewritten.

---

### STORY-059.2: Postgres schema for per-agent private memory

**Status:** planned  
**Size:** XL  
**Depends on:** STORY-059.1

#### Why

Private memory must scale with many agents without per-agent SQLite files; one cluster should host all agents with clear `(project_id, agent_id)` boundaries.

#### Acceptance criteria

- [ ] Private memories are stored in **Postgres tables** keyed by `project_id` (or repo root hash) and `agent_id`, with indexes suited to hot `recall` / BM25-adjacent queries as designed.
- [ ] Default layout **does not** create `.tapps-brain/agents/<id>/memory.db` for v3.
- [ ] Migrations are **forward-only** SQL files (no SQLite branches).
- [ ] Retrieval, decay, consolidation, and safety behaviors are **re-specified** against Postgres (behavioral parity where intended; breaking changes allowed if documented).
- [ ] Load test or benchmark smoke: N concurrent agents against one Postgres within agreed SLO (define in story).

#### Verification

- Targeted integration tests + migration tests against disposable Postgres.

---

### STORY-059.3: Single DSN and pool configuration

**Status:** planned  
**Size:** M  
**Depends on:** STORY-059.1

#### Why

One operational contract reduces misconfiguration between AgentForge, MCP, and CLI.

#### Acceptance criteria

- [ ] Documented env vars: e.g. `TAPPS_BRAIN_DATABASE_URL` **or** explicit `TAPPS_BRAIN_HIVE_DSN` / `TAPPS_BRAIN_FEDERATION_DSN` if they remain split—**one story outcome**: minimal variables, table in README.
- [ ] Connection **pool** sizing, connect timeout, and idle behavior configurable via env; defaults documented.
- [ ] Health / readiness exposes **pool saturation** and last migration version applied.
- [ ] No implicit silent fallback when URL is malformed.

#### Verification

- Unit tests for config parsing; integration test for pool exhaustion behavior (graceful errors).

---

### STORY-059.4: Test and dev ergonomics

**Status:** planned  
**Size:** M  
**Depends on:** STORY-059.2, STORY-059.3

#### Why

Postgres-only fails adoption if `uv run pytest` is harder than today.

#### Acceptance criteria

- [ ] Repo-root `docker-compose` (or documented profile) starts Postgres + optional pgvector image matching prod.
- [ ] `Makefile` or `justfile` / task: `brain-up`, `brain-test` (names TBD) documented in `AGENTS.md` or `README`.
- [ ] CI uses **ephemeral Postgres**; no flaky shared DB.
- [ ] Contributor onboarding: “clone → compose up → pytest” in **≤ N minutes** (state target in doc).
- [ ] Optional: seed script for minimal fixture data for demos.

#### Verification

- CI green; new contributor dry-run checklist in PR template or docs.

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
