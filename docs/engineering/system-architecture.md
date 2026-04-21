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
│  └─ postgres_private   │    │  │  └─ PostgresHiveBackend         │    │
│     └─ private_memories│    │  └─────────────────────────────────┘    │
│        table keyed by  │    │  ┌─────────────────────────────────┐    │
│        (project_id,    │    │  │ FederationBackend protocol      │    │
│         agent_id)      │    │  │  └─ PostgresFederationBackend   │    │
│                        │    │  └─────────────────────────────────┘    │
│  Isolated by row scope │    │                                         │
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
- **MCP server**: `tapps-brain-mcp` in `mcp_server/` package (7 focused submodules, TAP-605) — passes `--agent-id` through to `MemoryStore`.

## Primary components

- **Agent facade**: `agent_brain.py`
  - `AgentBrain` wraps `MemoryStore` + `HiveBackend`; agents never think about backends, scopes, or propagation
- **Store orchestration**: `store.py`
  - Save/update/delete, search, recall orchestration, maintenance, feedback, diagnostics, flywheel
  - Per-agent isolation via `agent_id` parameter (EPIC-053)
- **Persistence layer**: `postgres_private.py`
  - `PostgresPrivateBackend` — private-memory Postgres backend; schema migrations in `src/tapps_brain/migrations/private/` (001–014)
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

All durable stores live in **PostgreSQL** ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)). Per-agent isolation is enforced by a `(project_id, agent_id)` composite key on every row — not by separate database files.

| Store | Purpose | Backend | DSN / table |
|-------|---------|---------|-------------|
| **Private memory** | Per-agent isolated store | **PostgreSQL** — `private_memories` table | `TAPPS_BRAIN_DATABASE_URL` (`postgres://...`) |
| **Hive** | Cross-agent shared memory | **PostgreSQL** only ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)) | `TAPPS_BRAIN_HIVE_DSN` (`postgres://...`) |
| **Federation** | Cross-project sharing | **PostgreSQL** only ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)) | `TAPPS_BRAIN_FEDERATION_DSN` (`postgres://...`) |

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

Each agent gets its own `MemoryStore` backed by **Postgres rows scoped to `(project_id, agent_id)`**. 200 agents share one Postgres cluster but their rows never overlap. No cross-agent lock contention for private memory.

### Within a single agent

- **No async core:** Public APIs are synchronous. MCP hosts may call into `MemoryStore` from thread pools; each call still runs the sync stack to completion.
- **Process-local serialization:** `MemoryStore` uses a `threading.Lock` so **one mutating or read-heavy store operation at a time** per agent per process. This is fine because each agent has its own lock.
- **Lock ordering (EPIC-050 STORY-050.2):** Acquire the **store** lock (`MemoryStore`'s internal lock via `_serialized()`) **before** mutating `_entries` or other in-memory orchestration state, then call into `PostgresPrivateBackend` / Hive helpers as needed. **Do not** call back into public `MemoryStore` methods from deep inside persistence while holding only the store lock — that pattern risks deadlock because the store lock is non-reentrant.
- **Reentrancy:** The store lock is a plain `threading.Lock` (not RLock). **Never** invoke another `MemoryStore` public API from code that already runs under `_serialized()`.
- **Optional lock timeout:** Set **`TAPPS_STORE_LOCK_TIMEOUT_S`** to a positive float (seconds) or pass `lock_timeout_seconds=` when constructing `MemoryStore`.

### Shared store concurrency (Hive / Federation)

- **PostgreSQL:** MVCC handles concurrent reads/writes from N agents without locking. Connection pooling via `psycopg_pool` (`PostgresConnectionManager`) — configurable `min_size`/`max_size` via `TAPPS_BRAIN_HIVE_POOL_MIN`/`TAPPS_BRAIN_HIVE_POOL_MAX` env vars.
- All shared stores (Hive, Federation) require PostgreSQL ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)).

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
  - Private memory (`private_memories` table — PostgreSQL, keyed by `(project_id, agent_id)`)
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

## Docker deployment (EPIC-058, unified stack)

Reference files in `docker/`:

- `docker-compose.hive.yaml` — Unified stack: `tapps-brain-db` (pgvector/pg17) + `tapps-brain-migrate` (one-shot) + `tapps-brain-http` (HTTP + `/mcp/` + operator MCP) + optional `tapps-visual` dashboard.
- `init-db.sql` — Bootstraps the `vector` extension on first DB start.
- `Dockerfile.http` — Brain image (serves private memory + Hive + Federation on port 8080/8090).
- `Dockerfile.migrate` — Migrate-sidecar image. Entrypoint `migrate-entrypoint.sh` applies schemas, creates the DML-only `tapps_runtime` role, sets its password, then exits.
- `Dockerfile.visual` — nginx dashboard (reads `/snapshot` from the brain).
- `.env.example` — Template for the single secrets file `docker/.env` (DB password, runtime password, auth/admin tokens).
- `README.md` — Deployment guide.

See `docs/guides/hive-deployment.md` for full setup, `docs/guides/agentforge-integration.md` for connecting downstream projects.

## CLI distribution

The CLI is distributed as a Python package (`tapps-brain[cli]`) installed via pip or uv. The `tapps-brain --version` flag prints the installed package version.

### Single-binary distribution (future)

A single-binary build (e.g., via PyInstaller or Nuitka) has been requested by operations teams. This is tracked as a spike in EPIC-046 (STORY-046.2) and is **not yet scheduled**. Key considerations:

- Python runtime and optional native extensions (sentence-transformers, psycopg) must be bundled correctly.
- Binary size target: under 50 MB compressed.
- Signing and notarization for macOS distribution.
- CI matrix: Linux x86_64, macOS arm64, Windows x86_64.

Until a single-binary approach is validated, the recommended installation path remains `uv pip install tapps-brain[all]`.
