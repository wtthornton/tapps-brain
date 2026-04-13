# Data Stores and Schema Reference

## Store locations

All durable stores use **PostgreSQL** ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)). No SQLite fallback exists in v3.

| Store | Backend | DSN / table |
|-------|---------|-------------|
| **Private memory** (EPIC-053) | **PostgreSQL** — `private_memories` table | `TAPPS_BRAIN_DATABASE_URL` (`postgres://...`) |
| **Hive** (EPIC-054/055/059) | **PostgreSQL** only (ADR-007) | `TAPPS_BRAIN_HIVE_DSN` (`postgres://...`) |
| **Federation** (EPIC-054/055/059) | **PostgreSQL** only (ADR-007) | `TAPPS_BRAIN_FEDERATION_DSN` (`postgres://...`) |

For Postgres Hive schema and migrations, see `src/tapps_brain/migrations/hive/`. For deployment, see [`hive-deployment.md`](../guides/hive-deployment.md).

## Private memory store (PostgreSQL — `private_memories`)

Schema managed by `PostgresPrivateBackend` via migration files in `src/tapps_brain/migrations/private/`. Versioned in the `private_schema_version` table.

### Migration history

| Version | File | Added |
|---------|------|-------|
| 001 | `001_initial.sql` | `private_memories` table with `(project_id, agent_id, key)` PK; `tsvector` FTS column; pgvector HNSW index; `private_schema_version` tracking |
| 002 | `002_hnsw_upgrade.sql` | HNSW index upgrade (`m=16, ef_construction=200, vector_cosine_ops`) |
| 003 | `003_feedback_and_session.sql` | `feedback_events` table; `session_chunks` table |
| 004 | `004_diagnostics_history.sql` | `diagnostics_history` table |
| 005 | `005_audit_log.sql` | `audit_log` table (indexed on `project_id, agent_id, timestamp DESC`) |
| 006 | `006_gc_archive.sql` | `gc_archive` table (indexed on `project_id, agent_id, archived_at DESC`) |

### Core tables

- `private_memories` — keyed by `(project_id, agent_id, key)`, holds all entry fields + `tsvector` FTS column + pgvector `embedding` column (384-dim, HNSW cosine index)
- `feedback_events` — scoped to `(project_id, agent_id)` (migration 003)
- `session_chunks` — session-level context index (migration 003)
- `diagnostics_history` — periodic EWMA health snapshots (migration 004)
- `audit_log` — immutable append-only mutation log (migration 005)
- `gc_archive` — GC'd entries (never deleted; migration 006)
- `private_schema_version` — tracks applied migration versions

### Full-text search

- `tsvector` column on `private_memories` with GIN index; `setweight` on `key` (A), `value` (B), `tags` (C)
- Queried via `plainto_tsquery('english', ...)` in `PostgresPrivateBackend.search`

### Vector search

- pgvector `vector(384)` column with HNSW index (`m=16, ef_construction=200, cosine distance`) — migration 002
- Upgraded from IVFFlat in migration 002 for ~1.5× faster approximate-nearest-neighbor recall at comparable quality

### Entry cap and eviction (runtime)

The active `memories` row count is bounded by profile **`limits.max_entries`** (default **5000**). This is enforced in `MemoryStore.save`, not by a database trigger.

| Aspect | Behavior |
|--------|----------|
| **When** | Only when inserting a **new** key (`key` not already present) and the in-memory entry count is already at the cap. |
| **Policy** | Remove exactly **one** row: the entry with the **lowest stored `confidence`** value (the persisted field, not decay-adjusted effective confidence). |
| **Ties** | If several entries share the minimum confidence, Python’s `min` on the key iterable returns the **first** such key in **dict iteration order** (insertion order since 3.7). |
| **Persistence** | The evicted key is deleted from `private_memories` (`memory_evicted` is logged). |
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

### Semantic search

- **PostgreSQL:** `pgvector` extension, 384-dim `vector` column, L2 distance index

### Postgres schema migrations

SQL migration files in `src/tapps_brain/migrations/hive/`. Managed by `postgres_migrations.py` (`apply_hive_migrations()`). Version tracked in `hive_schema_version` table. Auto-migrate on startup: `TAPPS_BRAIN_HIVE_AUTO_MIGRATE=1`. CLI: `tapps-brain maintenance migrate-hive` / `hive-schema-status`.

## Federation hub (PostgreSQL — v3)

Same backend abstraction as Hive (EPIC-054/055). **PostgreSQL only** (ADR-007) — requires `TAPPS_BRAIN_FEDERATION_DSN` (`postgres://` or `postgresql://`). SQLite Federation was removed in v3.

### Core tables

- `federated_memories` (includes optional `memory_group` — publisher project-local partition on the hub; GitHub **#51** / 49-E)
- `federation_meta`

### Full-text search

- **PostgreSQL:** `tsvector` + GIN index

### Postgres schema migrations

SQL files in `src/tapps_brain/migrations/federation/`. Managed by `apply_federation_migrations()`. Version tracked in `federation_schema_version` table.

## Migration execution points

- `PostgresPrivateBackend` applies private migrations via `apply_private_migrations(dsn)` at initialization when `TAPPS_BRAIN_AUTO_MIGRATE=1` is set (or called explicitly).
- `MemoryStore` initializes `PostgresPrivateBackend`, so opening a store with auto-migrate enabled runs pending private migrations.
- CLI and MCP create stores through helper constructors, so both surfaces can run migrations on open.
- Hive and federation schema creation is managed by `apply_hive_migrations()` / `apply_federation_migrations()` respectively.

## Postgres full-text index

### Tokenizer

Private, Hive, and Federation stores all use PostgreSQL `to_tsvector('english', ...)` (Snowball stemmer, Unicode-aware). `setweight` applies column-level weighting:

| Weight | Column | Effect |
|--------|--------|--------|
| A | `key` | Highest match weight (exact key hits rank highest) |
| B | `value` | Standard content weight |
| C | `tags` | Lower weight for tag-field matches |

### Upgrade path

For higher ranking fidelity (true Okapi BM25 in SQL), the upgrade path to ParadeDB `pg_search` (BM25 on Tantivy) is documented in [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md). The current pure-Python BM25 in `bm25.py` provides BM25 semantics on top of Postgres `ts_rank_cd` results via Reciprocal Rank Fusion (`fusion.py`).

## Audit trail

### Table and schema (migration 005)

The append-only audit log lives in the `audit_log` Postgres table (migration 005 — `005_audit_log.sql`). The legacy `memory_log.jsonl` file was removed in the v3 Postgres-only persistence plane (ADR-007, EPIC-059 stage 2).

`AuditReader` (`audit.py`) queries `audit_log` via the `PostgresPrivateBackend.query_audit` method. It retains a fallback path for unit tests that pass a `Path` directly (JSONL legacy test mode).

| Column | Type | Description |
|--------|------|-------------|
| `project_id` | TEXT | Project identifier. |
| `agent_id` | TEXT | Agent identifier. |
| `id` | BIGSERIAL | Auto-incrementing row ID (part of PK). |
| `event_type` | TEXT | One of `save`, `delete`, `archive`, `reinforce`, `update_fields`, `consolidation_merge`, `consolidation_merge_undo`, etc. |
| `key` | TEXT | Memory entry key affected (empty string for non-key events). |
| `details` | JSONB | Additional details (tier, tags, reason, etc.) — varies by event type. |
| `timestamp` | TIMESTAMPTZ | UTC wall-clock time of the action. |

Indexes: chronological scan `(project_id, agent_id, timestamp DESC)`, per-key lookup `(project_id, agent_id, key)`, per-event-type filter `(project_id, agent_id, event_type)`.

### No rotation

The `audit_log` table is never truncated — it is a durable immutable record. Use standard Postgres archival / table partitioning for long-term retention management if needed.

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
