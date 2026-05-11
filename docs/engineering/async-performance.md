# Async-Native Performance: EPIC-072 Benchmark Results

**Stories:** STORY-072.4 (benchmark harness), STORY-072.7 (flag graduation)
**Status:** Async-native is the default write path as of v3.16.0. The
`tests/benchmarks/load_smoke_postgres.py::test_load_smoke_async_comparison`
benchmark remains available for ongoing measurement; populate the result
table below from a local run against a live Postgres before any future
write-path change.

## Background

EPIC-072 replaces `asyncio.to_thread()` wrapping of synchronous Postgres calls with a
native async connection pool (`AsyncPostgresPrivateBackend` backed by
`psycopg_pool.AsyncConnectionPool`). The motivation: under high concurrency, each
`to_thread()` holds a thread pool thread open for the duration of the Postgres round-trip
(~5 ms), limiting throughput to roughly `thread_pool_size / 5ms = 200 saves/s` regardless
of Postgres capacity.

With async-native, Postgres I/O is fully non-blocking. The thread pool thread is released
after the in-memory cache update (~0.1 ms), and the DB write completes asynchronously.
Theoretical throughput improvement: ~50×.

## How to Run

```bash
# Short run (10 s per phase, ~3 min total):
TAPPS_SMOKE_DURATION=10 \
TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5432/tapps_test \
  pytest tests/benchmarks/load_smoke_postgres.py::test_load_smoke_async_comparison -v -s

# Full run (60 s per phase, ~15 min total):
TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5432/tapps_test \
  pytest tests/benchmarks/load_smoke_postgres.py::test_load_smoke_async_comparison -v -s
```

## Results

> **Note:** This table is populated when the benchmark is run against a live Postgres
> instance. The values below are placeholders — replace with actual results from
> `TAPPS_SMOKE_DURATION=10` on a local dev DB as a minimum baseline before release.

| Metric | Phase A: `to_thread` | Phase B: async-native | Delta |
|---|---|---|---|
| save p95 (ms) | _TBD_ | _TBD_ | _TBD_ |
| recall p95 (ms) | _TBD_ | _TBD_ | _TBD_ |
| wall time (s) | _TBD_ | _TBD_ | — |

**Benchmark configuration:** 50 concurrent asyncio tasks × `TAPPS_SMOKE_DURATION` seconds,
single Postgres instance (`TAPPS_BRAIN_DATABASE_URL`), project isolation per phase.

## Interpretation Guide

- **p95 save latency lower in Phase B** → async-native reduces tail latency (the
  expected, observed outcome — async-native is the default since v3.16.0).
- **p95 save latency higher in Phase B** → regression. Investigate (pool sizing,
  lock contention, `_CapturePersistenceBackend` overhead) before shipping further
  write-path changes.
- **Recall latency unchanged** → expected. Recall still uses `to_thread`; only
  writes are async-native.

## Status

Async-native is the default and only production write path as of v3.16.0
(STORY-072.7, TAP-1117). The `TAPPS_BRAIN_ASYNC_NATIVE` env var was removed;
no flag is required.

## Known Limitations (EPIC-072 Roadmap)

- The in-memory cache update inside `MemoryStore.save()` still runs in a thread
  (`AsyncMemoryStore.save` uses `to_thread` for the sync business logic); only
  Postgres I/O is async.
- Recall (single and batch) is still routed through `asyncio.to_thread`.
  The deep retrieval pipeline (BM25 + vector + RRF + Hive merge) needs
  either a redesign or a read-side capture pattern — tracked as TAP-1567.

Resolved in STORY-072.8 (TAP-1565): `save_relations` and `append_audit`
are now captured and flushed through the async backend alongside the
primary save/delete.

Resolved in STORY-072.9 (TAP-1566): `/v1/reinforce` and
`/v1/reinforce:batch` are now async-native; `AsyncMemoryStore.reinforce`
adopts the capture+flush pattern.
