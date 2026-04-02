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

- Core operations are synchronous and lock-based.
- SQLite uses WAL mode where configured.
- Thread safety is handled via `threading.Lock` in store and persistence paths.

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
