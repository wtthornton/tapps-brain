---
id: EPIC-050
title: "Concurrency and runtime model — research and upgrades"
status: planned
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

**Status:** planned | **Effort:** S | **Depends on:** none  
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

**Status:** planned | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`, `tests/unit/test_memory_foundation_integration.py`, `tests/unit/test_concurrent.py`  
**Verification:** `pytest tests/unit/test_memory_foundation_integration.py tests/unit/test_concurrent.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Hold lock** duration metrics — identify **critical sections** that span SQLite I/O.
- **Reentrancy** hazards if one store method calls another (document forbidden patterns).

#### Implementation themes

- [ ] **Lock ordering** document (global order: `_lock` before persistence subcalls).
- [ ] Reduce **lock scope** where provably safe (formal review + stress test).
- [ ] **Timeout** on lock acquire with actionable error (deadlock hint).

---

### STORY-050.3: SQLite WAL, busy handling, and read scaling

**Status:** planned | **Effort:** M | **Depends on:** STORY-050.2  
**Context refs:** `src/tapps_brain/persistence.py` (PRAGMAs / connection lifecycle), SQLite upstream docs, `tests/unit/test_memory_persistence.py`, `tests/unit/test_persistence_sqlite_vec.py`  
**Verification:** `pytest tests/unit/test_memory_persistence.py tests/unit/test_persistence_sqlite_vec.py -v --tb=short -m "not benchmark"` (optional `pytest tests/benchmarks/ -m benchmark` for WAL stress)

#### Research notes (2026-forward)

- **busy_timeout** ms tuning; **WAL checkpoint** strategy for long-lived MCP servers.
- Evaluate **read_uncommitted** — generally **avoid**; document why.

#### Implementation themes

- [ ] Default **busy_timeout** env `TAPPS_SQLITE_BUSY_MS` (if not present).
- [ ] Operator **runbook**: “Database is locked” triage flowchart.
- [ ] Spike: **read connection** pool for search-only paths (thread-safe design required).

## Priority order

**050.1** (docs unblock) → **050.2** → **050.3**.
