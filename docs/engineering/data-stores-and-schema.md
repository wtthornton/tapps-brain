# Data Stores and Schema Reference

## Store locations

- **Project store**: `{project_root}/.tapps-brain/memory/memory.db`
- **Hive store**: `~/.tapps-brain/hive/hive.db`
- **Federation hub**: `~/.tapps-brain/memory/federated.db`

## Project store (`memory.db`)

### Core tables

- `schema_version`
- `memories`
- `archived_memories`
- `session_index`
- `relations`
- `feedback_events`
- `diagnostics_history`
- `flywheel_meta`

### FTS tables and triggers

- `memories_fts` + `memories_ai`, `memories_ad`, `memories_au`
- `session_index_fts` + `session_index_ai`, `session_index_ad`, `session_index_au`

### Notable indexes

- Tier/scope/confidence indexes on `memories`
- Temporal indexes (`valid_at`, `invalid_at`, `valid_from`, `valid_until`)
- `memory_group` index
- Feedback and diagnostics indexes for query/report paths

### Optional vector index

- `memory_vec` (`vec0`) via sqlite-vec when extension is available.

## Hive store (`hive.db`)

### Core tables

- `hive_memories`
- `hive_feedback_events`
- `hive_groups`
- `hive_group_members`
- `hive_write_notify`

### FTS

- `hive_fts` + sync triggers (`hive_fts_ai`, `hive_fts_ad`, `hive_fts_au`)

## Federation hub (`federated.db`)

### Core tables

- `federated_memories` (includes optional `memory_group` — publisher project-local partition on the hub; GitHub **#51** / 49-E)
- `federation_meta`

### FTS

- `federated_fts` content-linked to `federated_memories`

## Schema version timeline (`memory.db`)

Current schema version in `persistence.py`: **v17**

- v1: base memories, archive, FTS, core indexes
- v2: embeddings column
- v3: session index + FTS
- v4: relations
- v5: temporal validity (`valid_at`, `invalid_at`, `superseded_by`)
- v6: version marker bump for tooling/observability
- v7: `agent_scope`
- v8: `integrity_hash`
- v9: `feedback_events`
- v10: `diagnostics_history`
- v11: flywheel counters + `flywheel_meta`
- v12: provenance columns
- v13: `valid_from` and `valid_until`
- v14: `stability` and `difficulty`
- v15: Bayesian access counters
- v16: `memory_group` on active + archive tables
- v17: `embedding_model_id` on active + archive tables (dense provenance)

## Migration execution points

- `MemoryPersistence` runs `_ensure_schema()` at initialization.
- `MemoryStore` initializes `MemoryPersistence`, so opening a store runs migrations.
- CLI and MCP create stores through helper constructors, so both surfaces run migrations on open.
- Hive and federation schema creation is idempotent (`CREATE IF NOT EXISTS`) in respective store constructors.
