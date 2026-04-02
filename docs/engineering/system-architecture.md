# System Architecture (Implementation-Aligned)

## Runtime surfaces

- **Library API**: `MemoryStore` and related modules in `src/tapps_brain/`
- **CLI**: `tapps-brain` in `src/tapps_brain/cli.py`
- **MCP server**: `tapps-brain-mcp` in `src/tapps_brain/mcp_server.py`

## Primary components

- **Store orchestration**: `store.py`
  - Save/update/delete, search, recall orchestration, maintenance, feedback, diagnostics, flywheel
- **Persistence layer**: `persistence.py`
  - Project-local SQLite schema, migrations, FTS triggers, optional sqlite-vec path
- **Retrieval and injection**: `retrieval.py`, `injection.py`, `recall.py`
  - Composite scoring, optional hybrid/vector retrieval, token-budgeted injection
- **Hive (cross-agent sharing)**: `hive.py`
  - Shared SQLite store with namespaces, propagation engine, group membership, watch revision
- **Federation (cross-project sharing)**: `federation.py`
  - Hub SQLite store and explicit sync/publish
- **Quality loop**: `feedback.py`, `diagnostics.py`, `flywheel.py`
  - Signals, health scoring, anomaly/circuit behavior, report generation

## Data boundaries

- **Local project store**: `{project_root}/.tapps-brain/memory/memory.db`
- **Hive shared store**: `~/.tapps-brain/hive/hive.db`
- **Federation hub**: `~/.tapps-brain/memory/federated.db`

All three stores are SQLite-backed today.

## Feature and technology inventory

- **Industry features ↔ deps ↔ modules:** [`features-and-technologies.md`](features-and-technologies.md) (review-oriented map).

## Interface boundaries

- CLI and MCP are thin adapters over `MemoryStore` and supporting services.
- `MemoryStore` is the main in-process coordination boundary.
- Hive and federation are additive integration layers, not replacements for local project store operations.

## Concurrency model (current)

- **No async core:** Public APIs are synchronous. MCP hosts may call into `MemoryStore` from thread pools; each call still runs the sync stack to completion.
- **Process-local serialization:** `MemoryStore` uses a `threading.Lock` so **one mutating or read-heavy store operation at a time** per process for the in-memory cache and orchestration. Contended workloads queue on that lock before they touch SQLite.
- **Lock ordering (EPIC-050 STORY-050.2):** Acquire the **store** lock (`MemoryStore`’s internal lock via `_serialized()`) **before** mutating `_entries` or other in-memory orchestration state, then call into `MemoryPersistence` / Hive helpers as needed. `MemoryPersistence` uses its **own** lock around the SQLite connection. **Do not** call back into public `MemoryStore` methods from deep inside persistence while holding only the persistence lock — that pattern risks deadlock because the store lock is non-reentrant.
- **Reentrancy:** The store lock is a plain `threading.Lock` (not RLock). **Never** invoke another `MemoryStore` public API from code that already runs under `_serialized()` unless that inner path is proven not to acquire the store lock again (today: avoid nested public calls; use internal helpers that assume the lock is already held).
- **Optional lock timeout:** Set **`TAPPS_STORE_LOCK_TIMEOUT_S`** to a positive float (seconds) or pass `lock_timeout_seconds=` when constructing `MemoryStore`. If a thread cannot acquire the store lock within that window, `MemoryStoreLockTimeout` is raised instead of blocking indefinitely — useful to surface **contention** in tests or orchestration. Omit the env / param for the default **block until available** behavior.
- **Persistence:** `MemoryPersistence` (and Hive / federation stores) use their own locks around connection use. SQLite is opened in **WAL** mode where configured so readers and writers can overlap at the engine level, but **application-level locks still serialize** access from this codebase’s typical single-connection-per-store pattern.
- **SQLite busy timeout:** Set **`TAPPS_SQLITE_BUSY_MS`** (milliseconds) to control `PRAGMA busy_timeout` on memory, Hive, feedback, diagnostics, and federation hub connections. Default **5000** when unset or invalid. Operators troubleshooting **`database is locked`** should start here; see [`docs/guides/sqlite-database-locked.md`](../guides/sqlite-database-locked.md).
- **What operators should expect:** Many concurrent MCP clients or CLI scripts against **one** `MemoryStore` behave like a **single-lane road**: latency grows under parallel load; failures often show as **lock wait** or **database is locked** if SQLite busy limits are exceeded, not as silent corruption.
- **Scaling posture:** Throughput targets are **modest concurrent sessions**, not high-QPS multi-tenant service. If lock or `database is locked` errors appear in production, first tune **SQLite busy timeout** / workload separation; **queue or service extraction** is a product/architecture decision (see **EPIC-050**, **EPIC-051**, and `open-issues-roadmap.md` row 22), not a quick config tweak.

## High-level subsystem map

- **Ingress**
  - CLI commands
  - MCP tool calls
  - Direct library calls
- **Core**
  - Validation/safety
  - Save/retrieve/rank/inject
  - Lifecycle maintenance
  - Diagnostics/flywheel
- **Persistence**
  - Local memory DB
  - Hive DB
  - Federation DB
- **Egress**
  - Recall payloads
  - MCP resources
  - CLI reports
  - JSON/Markdown export
