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
- **Persistence:** `MemoryPersistence` (and Hive / federation stores) use their own locks around connection use. SQLite is opened in **WAL** mode where configured so readers and writers can overlap at the engine level, but **application-level locks still serialize** access from this codebase’s typical single-connection-per-store pattern.
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
