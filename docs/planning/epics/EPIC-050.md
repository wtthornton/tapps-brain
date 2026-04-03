---
id: EPIC-050
title: "Concurrency and runtime model — research and upgrades"
status: in_progress
priority: medium
created: 2026-03-31
tags: [concurrency, sqlite, threading, performance, wal]
---

# EPIC-050: Concurrency and runtime model

## Context

Maps to **§9** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) and roadmap backlog **“Concurrency documentation”**.

## Success criteria

- [x] Engineering doc answers: **how many concurrent MCP clients**, **what fails first**, **what to tune** — see [`system-architecture.md`](../../engineering/system-architecture.md) § *Concurrency model* (2026-04-01).

## Stories

**§9 row order:** **050.1** no async in core → **050.2** `threading.Lock` + SQLite discipline → **050.3** WAL / busy / read scaling.

### STORY-050.1: Synchronous API philosophy (no async core)

**Status:** done (2026-04-01) — optional async wrapper remains backlog | **Effort:** S | **Depends on:** none  
**Context refs:** `CLAUDE.md`, `src/tapps_brain/store.py` (sync API), `docs/engineering/system-architecture.md`  
**Verification:** doc-only (no pytest gate); merge engineering doc update + maintainer sign-off in this epic when done

#### Research notes (2026-forward)

- **AnyIO** / async adapters for MCP hosts are **host-side**; core can stay sync if **threadpool** wraps calls.
- **Risk:** async host + sync SQLite + thread explosion — document **max workers**.

#### Implementation themes

- [x] `docs/engineering/system-architecture.md` subsection: **threading model** (merged into § *Concurrency model*, 2026-04-01).
- [ ] Optional **async wrapper** package (`tapps_brain_async`) — spike only if demand.

---

### STORY-050.2: Thread safety (`threading.Lock` + SQLite discipline)

**Status:** done (2026-04-02) | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`, `tests/unit/test_memory_foundation_integration.py`, `tests/unit/test_concurrent.py`  
**Verification:** `pytest tests/unit/test_memory_foundation_integration.py tests/unit/test_concurrent.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Hold lock** duration metrics — identify **critical sections** that span SQLite I/O.
- **Reentrancy** hazards if one store method calls another (document forbidden patterns).

#### Implementation themes

- [x] **Lock ordering** + **reentrancy** — `docs/engineering/system-architecture.md` § *Concurrency model* (2026-04-02).
- [ ] Reduce **lock scope** where provably safe (formal review + stress test) — deferred; no behavior change beyond `_serialized()` wrapper.
- [x] **Timeout** on lock acquire — `TAPPS_STORE_LOCK_TIMEOUT_S` / `lock_timeout_seconds=` → `MemoryStoreLockTimeout`; tests in `test_concurrent.py`.

---

### STORY-050.3: SQLite WAL, busy handling, and read scaling

**Status:** done (2026-04-02) | **Effort:** M | **Depends on:** STORY-050.2  
**Context refs:** `src/tapps_brain/persistence.py` (PRAGMAs / connection lifecycle), SQLite upstream docs, `tests/unit/test_memory_persistence.py`, `tests/unit/test_persistence_sqlite_vec.py`  
**Verification:** `pytest tests/unit/test_memory_persistence.py tests/unit/test_persistence_sqlite_vec.py -v --tb=short -m "not benchmark"` (optional `pytest tests/benchmarks/ -m benchmark` for WAL stress)

#### Research notes (2026-forward)

- **busy_timeout** ms tuning.
- **WAL checkpoint** for long-lived MCP — operator guidance in [`sqlite-database-locked.md`](../../guides/sqlite-database-locked.md) § *WAL checkpoint* and [`openclaw-runbook.md`](../../guides/openclaw-runbook.md) § *Long-lived MCP and SQLite WAL* (2026-04-02).
- Evaluate **read_uncommitted** — generally **avoid**; document why.

#### Implementation themes

- [x] Default **busy_timeout** env `TAPPS_SQLITE_BUSY_MS` (if not present) — `resolve_sqlite_busy_timeout_ms()` in `sqlcipher_util.py`, federation hub aligned (2026-04-02).
- [x] Operator **runbook**: “Database is locked” triage flowchart — [`docs/guides/sqlite-database-locked.md`](../../guides/sqlite-database-locked.md) (2026-04-02).
- [x] Spike: **read connection** for search-only paths — opt-in ``TAPPS_SQLITE_MEMORY_READONLY_SEARCH``; second RO URI connection + ``_read_lock`` for FTS search + sqlite-vec KNN; fallback to writer on failure; runbook + ``connect_sqlite_readonly`` (2026-04-02).
- [x] Operator **WAL checkpoint** note (long-lived MCP): `sqlite-database-locked.md` + `openclaw-runbook.md` with links to SQLite pragma / WAL docs (2026-04-02).

## Priority order

**050.1** (docs unblock) → **050.2** → **050.3**.
