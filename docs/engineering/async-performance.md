# Async-Native Performance: EPIC-072 Benchmark Results

**Story:** STORY-072.4  
**Status:** Benchmark script ready; results require a live Postgres to populate.

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

- **p95 save latency lower in Phase B** → async-native reduces tail latency. Safe to default
  `TAPPS_BRAIN_ASYNC_NATIVE=1` in the next minor.
- **p95 save latency higher in Phase B** → regression. Document the finding here and defer
  making async-native the default. Keep `TAPPS_BRAIN_ASYNC_NATIVE=0` (current default) and
  investigate (pool sizing, lock contention, `_CapturePersistenceBackend` overhead).
- **Recall latency unchanged** → expected. Recall still uses `to_thread`; only writes are
  async-native in STORY-072.3–072.5.

## Feature Flag

`TAPPS_BRAIN_ASYNC_NATIVE=1` enables the async-native write path (default: `0`).
See `docs/guides/deployment.md` for deployment guidance and `CHANGELOG.md` for the
graduation timeline.

## Known Limitations (EPIC-072 Roadmap)

- Relations (`save_relations`) and audit log (`append_audit`) writes are no-ops in native
  mode — tracked in EPIC-072 roadmap items STORY-072.6+.
- The in-memory cache update inside `MemoryStore.save()` still runs in a thread
  (`_native_save` uses `to_thread` for CPU-bound logic); only Postgres I/O is async.
- Recall, reinforce, and batch write paths are not yet async-native (only `/v1/remember`,
  `/v1/forget`, `/v1/learn_success`, `/v1/learn_failure` are wired in STORY-072.5).
