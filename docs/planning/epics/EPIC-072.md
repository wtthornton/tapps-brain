---
id: EPIC-072
title: "Async-Native Postgres Core — psycopg3 AsyncConnection upgrade"
status: planned
priority: medium
created: 2026-04-15
tags: [async, postgres, psycopg3, performance, concurrency, v3]
depends_on: [EPIC-059, EPIC-066]
blocks: []
---

# EPIC-072: Async-Native Postgres Core — psycopg3 AsyncConnection Upgrade

## Goal

Replace the `asyncio.to_thread()` shim in `AsyncMemoryStore` with native `psycopg3` async connections (`psycopg.AsyncConnection`, `psycopg_pool.AsyncConnectionPool`) so that concurrent agent calls share a single non-blocking event loop instead of dispatching to a thread pool — improving latency under load and unlocking true async pipelining.

## Motivation

`AsyncMemoryStore` (v3.6.0) wraps the synchronous `MemoryStore` with `asyncio.to_thread()`. This works but has three structural costs:

1. **Thread pool contention** — each async call grabs a thread from `asyncio`'s default executor; under 50+ concurrent agents this queues behind the executor limit (default 64 on CPython).
2. **Connection pool fragmentation** — `psycopg_pool.ConnectionPool` (sync) blocks threads while waiting for connections; async agents waiting in the thread pool compound this.
3. **No async pipelining** — operations that could be pipelined (`save` + `propagate`) run sequentially because the sync pool doesn't support async pipelining.

`psycopg3` ships `AsyncConnection` and `AsyncConnectionPool` that are first-class event-loop-native. The migration is safe because `psycopg3`'s async and sync APIs share the same SQL dialect and parameter binding semantics.

**Prerequisite:** EPIC-059 and EPIC-066 confirmed Postgres-only; the sync pool is already `psycopg_pool.ConnectionPool`. psycopg3 async is a drop-in upgrade to the pool layer.

## Acceptance Criteria

- [ ] `AsyncConnectionPool` replaces `ConnectionPool` in `postgres_connection.py` for the async path (sync pool retained for CLI / sync `MemoryStore`)
- [ ] `postgres_private.py` has an `AsyncPostgresPrivateBackend` variant using `AsyncConnection`; sync `PostgresPrivateBackend` unchanged
- [ ] `AsyncMemoryStore` uses `AsyncPostgresPrivateBackend` directly — no `asyncio.to_thread()` on DB calls
- [ ] `threading.Lock` in `MemoryStore` is **not** shared with the async variant (separate instances)
- [ ] Load smoke benchmark (`tests/benchmarks/load_smoke_postgres.py`) re-run with 50 async agents; p95 save latency documented before/after
- [ ] All existing `AsyncMemoryStore` integration tests pass unchanged
- [ ] Feature flag or env var (`TAPPS_BRAIN_ASYNC_NATIVE=1`) allows opt-in initially, with plan to make default after one release cycle

## Stories

### STORY-072.1: AsyncConnectionPool in postgres_connection.py

**Status:** planned
**Size:** M
**Depends on:** —

#### Why

The pool is the root; everything downstream uses it.

#### Acceptance criteria

- [ ] `PostgresConnectionManager` exposes both `get_pool()` (sync) and `get_async_pool()` (returns `psycopg_pool.AsyncConnectionPool`).
- [ ] Async pool initialized lazily on first use; shares DSN config with sync pool.
- [ ] `TAPPS_BRAIN_PG_POOL_*` env vars apply to both pools.
- [ ] Unit tests: async pool connects, queries, closes cleanly.

#### Verification

- `pytest tests/unit/test_postgres_connection.py` — async pool tests.

---

### STORY-072.2: AsyncPostgresPrivateBackend

**Status:** planned
**Size:** L
**Depends on:** STORY-072.1

#### Why

The private backend is the hot path for every save/recall; this is where the thread-pool overhead shows.

#### Acceptance criteria

- [ ] `AsyncPostgresPrivateBackend` implements same protocol as `PostgresPrivateBackend` with `async def` methods.
- [ ] Uses `async with pool.connection() as conn` — no thread dispatch.
- [ ] All SQL queries identical to sync counterpart (no behavioral change).
- [ ] Unit tests: round-trip save/recall via async backend.

#### Verification

- `pytest tests/unit/test_async_private_backend.py`

---

### STORY-072.3: AsyncMemoryStore without asyncio.to_thread

**Status:** planned
**Size:** M
**Depends on:** STORY-072.2

#### Why

`AsyncMemoryStore` is the public API; removing `to_thread` is the payoff.

#### Acceptance criteria

- [ ] `AsyncMemoryStore` uses `AsyncPostgresPrivateBackend` when `TAPPS_BRAIN_ASYNC_NATIVE=1` (or always, after flag removal).
- [ ] No `asyncio.to_thread()` on any DB-touching call path.
- [ ] `threading.Lock` removed from `AsyncMemoryStore` (replaced by async-safe patterns or left to Postgres serialization).
- [ ] All existing `AsyncMemoryStore` integration tests pass.

#### Verification

- `pytest tests/integration/test_async_memory_store.py`

---

### STORY-072.4: Load smoke benchmark — before/after comparison

**Status:** planned
**Size:** M
**Depends on:** STORY-072.3

#### Why

The motivation is performance; measurement is the proof.

#### Acceptance criteria

- [ ] `tests/benchmarks/load_smoke_postgres.py` updated to benchmark async path (50 concurrent `asyncio` tasks, not threads).
- [ ] p95 save latency and recall latency recorded for: (a) `asyncio.to_thread` baseline, (b) async-native.
- [ ] Result documented in `docs/engineering/v3-behavioral-parity.md` or new `docs/engineering/async-performance.md`.
- [ ] If p95 regression (async-native slower than `to_thread`): document finding and defer STORY-072.3 feature flag default.

#### Verification

- Benchmark script run locally + results committed.

---

### STORY-072.5: HTTP adapter and MCP async wiring

**Status:** planned
**Size:** S
**Depends on:** STORY-072.3

#### Why

FastAPI routes are already async; they should use the async store directly without spawning threads.

#### Acceptance criteria

- [ ] HTTP adapter (`http_adapter.py`) uses `AsyncMemoryStore` (async-native) instead of sync store via `run_in_executor`.
- [ ] MCP Streamable HTTP handler (`mcp_server/`) uses async store if invoked in async context.
- [ ] Integration test: concurrent HTTP requests complete without thread pool saturation.

#### Verification

- `pytest tests/integration/test_http_adapter.py` — concurrent request test.

---

### STORY-072.6: Feature flag graduation and deprecation plan

**Status:** planned
**Size:** S
**Depends on:** STORY-072.4, STORY-072.5

#### Why

Safe rollout: opt-in first, then default after one cycle with observed production behavior.

#### Acceptance criteria

- [ ] `TAPPS_BRAIN_ASYNC_NATIVE=1` enables async-native path; default is `0` for this release.
- [ ] CHANGELOG entry: "async-native mode available via flag; will become default in next minor."
- [ ] `docs/guides/deployment.md` updated with flag and migration note.
- [ ] Tracking issue created for flag removal in next cycle.

#### Verification

- CHANGELOG + docs review.

## Out of scope

- Async Hive backend (`AsyncPostgresHiveBackend`) — parallel effort, not blocking
- Replacing the sync `MemoryStore` for CLI use cases (sync stays sync)
- gRPC transport (not planned for v3)

## Risk

**Psycopg3 async pool initialization in FastAPI lifespan** — async pool must be initialized in an async context (FastAPI `lifespan` handler), not at module import time. The current sync pool uses lazy init; the async pool needs the same pattern but in an async-safe way. See psycopg3 docs on `AsyncConnectionPool.open()`.

## References

- `src/tapps_brain/aio.py` — current `AsyncMemoryStore` implementation
- `src/tapps_brain/postgres_connection.py` — current sync pool
- [psycopg3 async connection pool docs](https://www.psycopg.org/psycopg3/docs/api/pool.html#psycopg_pool.AsyncConnectionPool)
- [EPIC-059](EPIC-059.md) — Postgres-only foundation
- [EPIC-066](EPIC-066.md) — Production readiness (pool tuning baseline)
- `tests/benchmarks/load_smoke_postgres.py` — existing benchmark to extend
