# System Architecture (Implementation-Aligned)

## Design for scale: 200+ concurrent agents

tapps-brain is designed for **many concurrent agents** without shared-DB bottlenecks:

```
┌──────────────────────────────────────────────────────────────────────┐
│                           INGRESS                                    │
│  AgentBrain API  │  CLI (tapps-brain)  │  MCP Server  │  Library    │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│                  AGENT FACADE: AgentBrain (agent_brain.py)           │
│  remember() │ recall() │ forget() │ learn_from_success/failure()     │
│  Agents use this — never MemoryStore directly                        │
└──────┬───────────────────────────────┬───────────────────────────────┘
       │                               │
┌──────▼────────────────┐    ┌─────────▼───────────────────────────────┐
│  PRIVATE MEMORY       │    │  SHARED MEMORY (Backend Abstraction)     │
│  (per-agent, isolated) │    │                                         │
│                        │    │  ┌─────────────────────────────────┐    │
│  MemoryStore           │    │  │ HiveBackend protocol            │    │
│  └─ persistence.py     │    │  │  └─ PostgresHiveBackend         │    │
│     └─ agent's own     │    │  └─────────────────────────────────┘    │
│        memory.db       │    │  ┌─────────────────────────────────┐    │
│                        │    │  │ FederationBackend protocol      │    │
│  No contention between │    │  │  └─ PostgresFederationBackend   │    │
│  agents — each has its │    │  └─────────────────────────────────┘    │
│  own SQLite + lock.    │    │                                         │
│                        │    │  Factory: create_hive_backend(dsn)      │
└────────────────────────┘    │  Requires postgres:// DSN (ADR-007)     │
                              │                                         │
┌────────────────────────┐    │  See: ADR-007-postgres-only-no-sqlite   │
│  RETRIEVAL STACK       │    └─────────────────────────────────────────┘
│  retrieval.py          │
│  fusion.py (RRF)       │    ┌─────────────────────────────────────────┐
│  bm25.py               │    │  QUALITY LOOP                           │
│  embeddings.py         │    │  feedback.py │ diagnostics.py           │
│  reranker.py           │    │  flywheel.py │ evaluation.py            │
│  injection.py          │    └─────────────────────────────────────────┘
│  decay.py              │
└────────────────────────┘
```

## Runtime surfaces

- **AgentBrain API**: `agent_brain.py` — primary interface for agents (EPIC-057). 5 methods: `remember()`, `recall()`, `forget()`, `learn_from_success()`, `learn_from_failure()`. Configured via env vars or constructor.
- **Library API**: `MemoryStore` in `store.py` — lower-level API with full control over save, search, maintenance, diagnostics.
- **CLI**: `tapps-brain` in `cli.py` — accepts `--agent-id` for per-agent operations.
- **MCP server**: `tapps-brain-mcp` in `mcp_server.py` — passes `--agent-id` through to `MemoryStore`.

## Primary components

- **Agent facade**: `agent_brain.py`
  - `AgentBrain` wraps `MemoryStore` + `HiveBackend`; agents never think about backends, scopes, or propagation
- **Store orchestration**: `store.py`
  - Save/update/delete, search, recall orchestration, maintenance, feedback, diagnostics, flywheel
  - Per-agent isolation via `agent_id` parameter (EPIC-053)
- **Persistence layer**: `persistence.py`
  - Per-agent SQLite schema, migrations (v1→v17), FTS triggers, optional sqlite-vec path
- **Backend abstraction**: `_protocols.py`, `backends.py`
  - `HiveBackend`, `FederationBackend`, `AgentRegistryBackend` protocols
  - `create_hive_backend(dsn)` / `create_federation_backend(dsn)` factories
  - SQLite backends removed (ADR-007); Hive/Federation require PostgreSQL
- **PostgreSQL backends**: `postgres_hive.py`, `postgres_federation.py`, `postgres_connection.py`, `postgres_migrations.py`
  - `PostgresHiveBackend` — full Hive with pgvector, tsvector, LISTEN/NOTIFY, connection pooling
  - `PostgresFederationBackend` — full Federation with parameterized SQL, JSONB tags
  - `PostgresConnectionManager` — psycopg + psycopg_pool
  - Versioned schema migrations in `src/tapps_brain/migrations/`
- **Retrieval and injection**: `retrieval.py`, `injection.py`, `recall.py`
  - Composite scoring, optional hybrid/vector retrieval, token-budgeted injection
- **Hive (cross-agent sharing)**: `postgres_hive.py` (PostgreSQL only — ADR-007)
  - Shared store with namespaces, propagation engine, group membership, expert publishing, watch revision
- **Federation (cross-project sharing)**: `postgres_federation.py` (PostgreSQL only — ADR-007)
  - Hub store and explicit sync/publish
- **Quality loop**: `feedback.py`, `diagnostics.py`, `flywheel.py`
  - Signals, health scoring, anomaly/circuit behavior, report generation

## Data boundaries

| Store | Purpose | Backend | Path / DSN |
|-------|---------|---------|------------|
| **Agent memory** | Private per-agent store | SQLite (isolated) | `{project}/.tapps-brain/agents/{agent_id}/memory.db` |
| **Legacy memory** | Shared project store (pre-v3.1) | SQLite | `{project}/.tapps-brain/memory/memory.db` |
| **Hive** | Cross-agent shared memory | **PostgreSQL** only ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)) | `TAPPS_BRAIN_HIVE_DSN` (`postgres://...`) |
| **Federation** | Cross-project sharing | **PostgreSQL** only ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)) | `TAPPS_BRAIN_FEDERATION_DSN` (`postgres://...`) |

**Why this split:** Agent-local memory is fast embedded SQLite with no cross-agent contention. Shared stores (Hive/Federation) require PostgreSQL for concurrent multi-host access — MVCC, connection pooling, pgvector, and LISTEN/NOTIFY ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)).

## Feature and technology inventory

- **Industry features ↔ deps ↔ modules:** [`features-and-technologies.md`](features-and-technologies.md) (review-oriented map).

## Interface boundaries

- `AgentBrain` is the recommended entry point for agents — wraps `MemoryStore` + configured Hive backend.
- CLI and MCP are thin adapters over `MemoryStore` and supporting services.
- `MemoryStore` is the main in-process coordination boundary for a single agent.
- Hive and federation are additive shared layers, not replacements for local agent store operations.
- Backend selection is by configuration (DSN string), not by import — callers program against protocols.

## Concurrency model

### Per-agent isolation (the scaling model)

Each agent gets its own `MemoryStore` backed by its own SQLite file. **200 agents = 200 independent SQLite databases.** No shared locks, no contention between agents for private memory.

### Within a single agent

- **No async core:** Public APIs are synchronous. MCP hosts may call into `MemoryStore` from thread pools; each call still runs the sync stack to completion.
- **Process-local serialization:** `MemoryStore` uses a `threading.Lock` so **one mutating or read-heavy store operation at a time** per agent per process. This is fine because each agent has its own lock.
- **Lock ordering (EPIC-050 STORY-050.2):** Acquire the **store** lock (`MemoryStore`'s internal lock via `_serialized()`) **before** mutating `_entries` or other in-memory orchestration state, then call into `MemoryPersistence` / Hive helpers as needed. `MemoryPersistence` uses its **own** lock around the SQLite connection. **Do not** call back into public `MemoryStore` methods from deep inside persistence while holding only the persistence lock — that pattern risks deadlock because the store lock is non-reentrant.
- **Reentrancy:** The store lock is a plain `threading.Lock` (not RLock). **Never** invoke another `MemoryStore` public API from code that already runs under `_serialized()`.
- **Optional lock timeout:** Set **`TAPPS_STORE_LOCK_TIMEOUT_S`** to a positive float (seconds) or pass `lock_timeout_seconds=` when constructing `MemoryStore`.

### Shared store concurrency (Hive / Federation)

- **PostgreSQL (production):** MVCC handles concurrent reads/writes from N agents without locking. Connection pooling via `psycopg_pool` (`PostgresConnectionManager`) — configurable `min_size`/`max_size` via `TAPPS_BRAIN_HIVE_POOL_MIN`/`TAPPS_BRAIN_HIVE_POOL_MAX` env vars.
- **Note:** SQLite Hive/Federation backends were removed (ADR-007). Shared stores require PostgreSQL.

### Agent-local SQLite tuning

- **SQLite WAL mode:** Enabled where configured so readers and writers can overlap at the engine level. Optionally (**``TAPPS_SQLITE_MEMORY_READONLY_SEARCH``**) FTS search and sqlite-vec KNN use a **read-only** second connection.
- **SQLite busy timeout:** Set **`TAPPS_SQLITE_BUSY_MS`** (milliseconds) to control `PRAGMA busy_timeout`. Default **5000**. See [`docs/guides/sqlite-database-locked.md`](../guides/sqlite-database-locked.md).

## High-level subsystem map

- **Ingress**
  - AgentBrain API (agents)
  - CLI commands
  - MCP tool calls
  - Direct library calls
- **Core**
  - Validation/safety
  - Save/retrieve/rank/inject
  - Lifecycle maintenance
  - Diagnostics/flywheel
- **Persistence**
  - Per-agent local memory DB (SQLite)
  - Shared Hive DB (PostgreSQL — ADR-007)
  - Federation DB (PostgreSQL — ADR-007)
- **Egress**
  - Recall payloads
  - MCP resources
  - CLI reports
  - JSON/Markdown export

## Observability

tapps-brain instruments the hot paths with OpenTelemetry traces and metrics.  The
`opentelemetry-api` package is a required dependency (no-op when no SDK is
configured).  Install `tapps-brain[otel]` for actual export.

### Canonical span names

All spans use constants from `tapps_brain.otel_tracer` — never string literals.

| Constant | Span name | Operation |
|----------|-----------|-----------|
| `SPAN_REMEMBER` | `tapps_brain.remember` | `MemoryStore.save` / `AgentBrain.remember` |
| `SPAN_RECALL` | `tapps_brain.recall` | `AgentBrain.recall` |
| `SPAN_SEARCH` | `tapps_brain.search` | Low-level BM25 + vector retrieval |
| `SPAN_HIVE_PROPAGATE` | `tapps_brain.hive.propagate` | Hive write fan-out |
| `SPAN_HIVE_SEARCH` | `tapps_brain.hive.search` | Hive cross-agent search |

Do **not** invent span names outside this table without updating this file and
`docs/operations/telemetry-policy.md`.

### Telemetry policy

Only bounded-enum attributes are permitted on spans and metrics — never raw
memory content, entry keys, query strings, or session/agent IDs.  See
[`docs/operations/telemetry-policy.md`](../operations/telemetry-policy.md) for
the full attribute allow-list, forbidden list, and log-redaction rules.

### HTTP trace context

The HTTP adapter (`http_adapter.py`) extracts W3C `traceparent` from incoming
request headers and passes it to `start_span()` so inbound spans are correctly
parented to upstream callers (STORY-061.3).

### Key modules

- `otel_tracer.py` — span names, `start_span()` context manager, `extract_trace_context()`
- `otel_exporter.py` — `OTelExporter` (MetricsSnapshot → OTel metrics), `MemoryBodyRedactionFilter`, `create_allowed_attribute_views()`
- `metrics.py` — `MetricsCollector`, `MetricsSnapshot`, `MetricsTimer`

---

## Docker deployment (EPIC-058)

Reference files in `docker/`:

- `docker-compose.hive.yaml` — Postgres container with pgvector (pgvector/pgvector:pg17)
- `init-hive.sql` — Schema initialization
- `Dockerfile.migrate` — Migration runner
- `README.md` — Deployment guide

See `docs/guides/hive-deployment.md` for full setup, `docs/guides/agentforge-integration.md` for connecting downstream projects.

## CLI distribution

The CLI is distributed as a Python package (`tapps-brain[cli]`) installed via pip or uv. The `tapps-brain --version` flag prints the installed package version.

### Single-binary distribution (future)

A single-binary build (e.g., via PyInstaller or Nuitka) has been requested by operations teams. This is tracked as a spike in EPIC-046 (STORY-046.2) and is **not yet scheduled**. Key considerations:

- SQLite and optional native extensions (sqlite-vec) must be bundled correctly.
- Binary size target: under 50 MB compressed.
- Signing and notarization for macOS distribution.
- CI matrix: Linux x86_64, macOS arm64, Windows x86_64.

Until a single-binary approach is validated, the recommended installation path remains `uv pip install tapps-brain[all]`.
