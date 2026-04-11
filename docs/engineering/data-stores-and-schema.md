# Data Stores and Schema Reference

## Store locations

| Store | Backend | Location |
|-------|---------|----------|
| **Agent store** (EPIC-053) | SQLite (per-agent, isolated) | `{project}/.tapps-brain/agents/{agent_id}/memory.db` |
| **Legacy project store** | SQLite | `{project}/.tapps-brain/memory/memory.db` |
| **Hive** (EPIC-054/055/059) | **PostgreSQL** only (ADR-007) | `TAPPS_BRAIN_HIVE_DSN` (`postgres://...`) |
| **Federation** (EPIC-054/055/059) | **PostgreSQL** only (ADR-007) | `TAPPS_BRAIN_FEDERATION_DSN` (`postgres://...`) |

For Postgres Hive schema and migrations, see `src/tapps_brain/migrations/hive/`. For deployment, see [`hive-deployment.md`](../guides/hive-deployment.md).

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

- `memory_vec` (`vec0`) via sqlite-vec when extension is available. Operator playbook (rebuild, VACUUM, distance metric, save-path cost): [`sqlite-vec-operators.md`](../guides/sqlite-vec-operators.md).

### Entry cap and eviction (runtime)

The active `memories` row count is bounded by profile **`limits.max_entries`** (default **5000**). This is enforced in `MemoryStore.save`, not by a database trigger.

| Aspect | Behavior |
|--------|----------|
| **When** | Only when inserting a **new** key (`key` not already present) and the in-memory entry count is already at the cap. |
| **Policy** | Remove exactly **one** row: the entry with the **lowest stored `confidence`** value (the persisted field, not decay-adjusted effective confidence). |
| **Ties** | If several entries share the minimum confidence, Python’s `min` on the key iterable returns the **first** such key in **dict iteration order** (insertion order since 3.7). |
| **Persistence** | The evicted key is deleted from SQLite (`memory_evicted` is logged). |
| **Not evicted on** | Updates to an existing key, deletes, or saves that only reinforce/replace the same key. |

### Per-`memory_group` cap (optional)

When profile **`limits.max_entries_per_group`** is set (integer ≥ 1), each bucket keyed by stored `memory_group` is capped independently. **`None` / empty normalization** (ungrouped rows) form one bucket, distinct from named groups.

| Aspect | Behavior |
|--------|----------|
| **When** | Inserts of a **new** key that would raise the target bucket above the cap, or **updates** that **change** `memory_group` into a bucket that would exceed the cap after the move. |
| **Policy** | Remove **one** row in that bucket: **lowest stored `confidence`**, same tie-breaking as the global policy (dict iteration order). Logged as `memory_evicted` with `reason=max_entries_per_group`. |
| **Global cap interaction** | Per-bucket enforcement runs **first** for the row about to be written. When a **new** key also hits the global `limits.max_entries`, one additional eviction runs. If per-group mode is enabled, that global eviction **prefers** the incoming row’s bucket (`reason=max_entries_fair`); if that bucket has no rows (only possible when falling back), eviction uses the **store-wide** lowest confidence (`reason=max_entries`). |
| **Sizing** | Operators should keep `max_entries_per_group` ≤ `max_entries` so both limits can be satisfiable; the store does not auto-clamp. |

When `max_entries_per_group` is **unset** (`null` / omitted), behavior matches the global-only policy above.

## Hive store (PostgreSQL only — ADR-007)

The Hive uses **PostgreSQL** exclusively (`TAPPS_BRAIN_HIVE_DSN`). SQLite Hive backends were removed in EPIC-059 (ADR-007).

### Core tables (shared across backends)

- `hive_memories`
- `hive_feedback_events`
- `hive_groups`
- `hive_group_members`
- `hive_write_notify`

### Full-text search

- **PostgreSQL:** `tsvector` column + GIN index + `plainto_tsquery()`
- *(SQLite Hive FTS removed — ADR-007)*

### Semantic search

- **PostgreSQL:** `pgvector` extension, 384-dim `vector` column, L2 distance index
- **SQLite:** `sqlite-vec` `memory_vec` table (when extension available)

### Postgres schema migrations

SQL migration files in `src/tapps_brain/migrations/hive/`. Managed by `postgres_migrations.py` (`apply_hive_migrations()`). Version tracked in `hive_schema_version` table. Auto-migrate on startup: `TAPPS_BRAIN_HIVE_AUTO_MIGRATE=1`. CLI: `tapps-brain maintenance migrate-hive` / `hive-schema-status`.

## Federation hub (PostgreSQL or SQLite)

Same backend abstraction as Hive (EPIC-054/055). PostgreSQL when `TAPPS_BRAIN_FEDERATION_DSN` is set, SQLite (`federated.db`) otherwise.

### Core tables

- `federated_memories` (includes optional `memory_group` — publisher project-local partition on the hub; GitHub **#51** / 49-E)
- `federation_meta`

### Full-text search

- **PostgreSQL:** `tsvector` + GIN index
- **SQLite:** `federated_fts` content-linked to `federated_memories`

### Postgres schema migrations

SQL files in `src/tapps_brain/migrations/federation/`. Managed by `apply_federation_migrations()`. Version tracked in `federation_schema_version` table.

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

## SQLite PRAGMAs set on open

Every connection opened via `sqlcipher_util.connect_sqlite()` applies the following PRAGMAs before returning. Read-only connections (`connect_sqlite_readonly()`) skip `journal_mode` but apply the rest.

| PRAGMA | Value | Rationale |
|--------|-------|-----------|
| `journal_mode` | `WAL` | Write-Ahead Logging enables concurrent readers during writes; critical for MCP burst patterns where search and save overlap. |
| `synchronous` | `NORMAL` | Balances durability and throughput. WAL + NORMAL guarantees crash consistency (no corruption) but may lose the last committed transaction on OS crash. `FULL` would add ~2x fsync cost per commit with marginal gain under WAL. |
| `busy_timeout` | `5000` ms (default, env `TAPPS_SQLITE_BUSY_MS`, clamped 0..3 600 000) | Retries internally for up to the timeout before raising `SQLITE_BUSY`. Avoids spurious "database is locked" under light contention. |
| `foreign_keys` | `ON` | Enforces referential integrity for `relations`, `feedback_events`, and future FK-linked tables. |
| `cache_size` | SQLite default (`-2000`, ~2 MB) | Not explicitly set; the default page cache is sufficient for typical project stores (<50 MB). Operators needing larger caches can set `PRAGMA cache_size` via a startup hook. |

### Read-only search connection (EPIC-050.3)

When `TAPPS_SQLITE_MEMORY_READONLY_SEARCH=1`, `MemoryPersistence` opens a second connection via `connect_sqlite_readonly()` using a `file:` URI with `mode=ro`. This handle serves FTS `search()` and sqlite-vec KNN queries so they do not serialize behind the writer lock. The read-only connection:

- Cannot mutate schema or data (enforced by SQLite URI mode).
- Shares the WAL, so reads see the latest committed snapshot.
- Falls back to the writer connection if the read-only open fails (logged at DEBUG).

### sqlite_busy_count metric

`MemoryStore` increments a `store.sqlite_busy_count` counter each time a persistence call raises `sqlite3.OperationalError` containing "database is locked". This counter is visible in `get_metrics().counters` and serves as an early warning that `busy_timeout` may need tuning or write serialization is required.

## Migration execution points

- `MemoryPersistence` runs `_ensure_schema()` at initialization.
- `MemoryStore` initializes `MemoryPersistence`, so opening a store runs migrations.
- CLI and MCP create stores through helper constructors, so both surfaces run migrations on open.
- Hive and federation schema creation is idempotent (`CREATE IF NOT EXISTS`) in respective store constructors.

## FTS5 full-text index

### Tokenizer

All FTS5 virtual tables (`memories_fts`, `session_index_fts`, `hive_fts`, `federated_fts`) use the **default `unicode61` tokenizer**. This tokenizer handles Unicode text, removes diacritics, and splits on whitespace and punctuation. It is a good default for mixed natural-language and technical content (code identifiers, file paths split on `/` and `.`).

### Sync triggers

FTS content tables are kept in sync with their base tables via `AFTER INSERT`, `AFTER DELETE`, and `AFTER UPDATE` triggers (e.g., `memories_ai`, `memories_ad`, `memories_au`). This means:

- Insertions are immediately searchable with no explicit rebuild step.
- Deletions and updates maintain FTS consistency automatically.
- No application-level FTS maintenance is required under normal operation.

### Rebuild command

If the FTS index becomes inconsistent (e.g., after a crash during a non-WAL write, or after a manual `DELETE` bypassing triggers), rebuild with:

```sql
INSERT INTO memories_fts(memories_fts) VALUES('rebuild');
```

Replace `memories_fts` with the appropriate FTS table name for other stores.

### Tokenizer change policy

Changing the tokenizer (e.g., from `unicode61` to `porter` or a custom tokenizer) is a **breaking change** that requires:

1. A **major schema version bump** (not a minor migration step).
2. A full FTS rebuild on all affected tables.
3. Documentation and changelog entry for operators.

Do not change the tokenizer in a patch or minor release.

## Audit trail

### File and format

The append-only audit log is written to `{store_dir}/memory_log.jsonl` (typically `.tapps-brain/memory/memory_log.jsonl`). Each line is a JSON object with at minimum:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO 8601 string | UTC wall-clock time of the action. |
| `action` | string | One of `save`, `delete`, `archive`, `reinforce`, `update_fields`, etc. |
| `key` | string | The memory entry key affected. |

Additional fields vary by action (e.g., `value`, `tier`, `tags` on `save`; `reason` on `delete`).

### Rotation

The audit log is automatically truncated when it exceeds **10,000 lines** (`_MAX_AUDIT_LINES` in `persistence.py`). Truncation keeps the most recent lines and discards the oldest.

**Operator recommendation:** Back up `memory_log.jsonl` before it reaches the rotation threshold if you need a complete audit history. Consider a cron job or pre-session hook that copies the file to long-term storage when `wc -l` approaches 9,000.

## Logging conventions

### Event naming

Structured log events use snake_case names scoped by subsystem:

| Event | Level | When |
|-------|-------|------|
| `memory_save` | INFO | Entry saved to persistence. |
| `memory_recall` | INFO | Entry retrieved by key or search. |
| `memory_delete` | INFO | Entry deleted from persistence. |
| `memory_evicted` | INFO | Entry removed due to cap enforcement. |
| `schema_migration_applied` | DEBUG | A schema migration step executed (with `from_version`, `to_version`). |
| `database_corrupt` | ERROR | Database failed to open or validate. |
| `memory_readonly_conn_failed` | DEBUG | Read-only search connection could not be opened. |
| `embedding_compute_failed` | DEBUG | Embedding generation failed for an entry. |

### Log levels

| Level | Usage |
|-------|-------|
| **DEBUG** | Internal diagnostics: migration steps, connection lifecycle, fallback paths. |
| **INFO** | Normal operations visible to operators: save, recall, delete, startup. |
| **WARNING** | Anomalies that may need attention: rate limit hits, degraded features, high contention. |
| **ERROR** | Failures requiring investigation: corrupt database, unrecoverable persistence errors. |

### structlog key conventions

- Use `key=` for memory entry keys, `path=` for file paths, `version=` for schema versions.
- Avoid logging full entry values at INFO (PII risk); use DEBUG with truncation if needed.
- Include `exc_info=True` on ERROR and unexpected exceptions at DEBUG.
