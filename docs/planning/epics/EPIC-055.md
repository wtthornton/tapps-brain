---
id: EPIC-055
title: "PostgreSQL Hive & Federation Backend"
status: done
priority: high
created: 2026-04-08
tags: [postgres, hive, federation, pgvector, scaling, multi-host]
completed: 2026-04-09
---

# EPIC-055: PostgreSQL Hive & Federation Backend

## Context

EPIC-054 introduced backend protocols for `HiveBackend` and `FederationBackend` with SQLite adapters. This epic implements the **PostgreSQL backends** — the core enabler for multi-host, multi-project, concurrent agent deployments.

**Why Postgres for Hive/Federation (not local stores):**

| Concern | Local agent store | Hive / Federation |
|---------|-------------------|-------------------|
| Access pattern | Single agent, single process | N agents × M projects, concurrent |
| Location | Same container as agent | Shared across containers/hosts |
| Concurrency | Single `threading.Lock` is fine | SQLite WAL breaks on network FS |
| Latency sensitivity | Every save/recall (hot path) | Cross-agent sharing (warm path) |
| Right backend | SQLite (embedded, fast) | Postgres (network-native, MVCC) |

**Key design decisions:**
- `pgvector` extension replaces `sqlite-vec` for ANN semantic search in shared stores
- `tsvector`/`tsquery` replaces FTS5 for full-text search
- `LISTEN/NOTIFY` replaces file-based write-notify polling
- Connection pooling via `psycopg_pool` (not PgBouncer — keep it in-process for simplicity)
- All Postgres operations are synchronous (matching tapps-brain's sync-only core per CLAUDE.md)

**Depends on:** EPIC-054 (backend protocols must exist)
**Enables:** EPIC-056 (groups), EPIC-057 (unified API), EPIC-058 (Docker deployment)

## Success Criteria

- [x] `create_hive_backend("postgres://...")` returns a working `PostgresHiveBackend`
- [x] All `HiveBackend` protocol methods implemented and passing protocol conformance tests
- [x] `pgvector` 384-dim index for semantic search across Hive memories
- [x] `tsvector` GIN index for full-text search
- [x] `LISTEN/NOTIFY` for real-time write notifications (replacing file polling)
- [x] Connection pooling with configurable min/max connections
- [x] Schema migration tooling (versioned, forward-only)
- [x] All existing Hive behavioral tests pass against Postgres backend
- [x] Federation backend implemented with same coverage
- [x] Graceful fallback: if Postgres is unreachable, agent local memory still works

## Stories

### STORY-055.1: PostgreSQL schema design for Hive

**Status:** done (2026-04-09)
**Effort:** L
**Depends on:** EPIC-054 (protocols defined)
**Context refs:** `src/tapps_brain/hive.py` (SQLite schema in `_ensure_schema`), `src/tapps_brain/persistence.py` (local schema for reference)
**Verification:** `pytest tests/unit/test_postgres_hive_schema.py -v --tb=short -m "not benchmark"`

#### Why

The Hive SQLite schema uses FTS5 triggers, `json_each()` for tag queries, and custom collation. Postgres has different capabilities (native JSONB, GIN indexes, tsvector). The schema must be designed for Postgres idioms, not ported line-by-line.

#### Acceptance Criteria

- [x] `hive_memories` table with columns matching `HiveStore.save()` parameters:
  - `namespace`, `key`, `value`, `source_agent`, `tier`, `confidence`, `source`
  - `tags` as `JSONB` (not TEXT) for native array queries
  - `valid_at`, `invalid_at`, `superseded_by` (temporal)
  - `memory_group`, `conflict_policy`
  - `embedding` as `vector(384)` via pgvector
  - `created_at`, `updated_at` timestamps with timezone
  - `search_vector` as `tsvector` (auto-updated via trigger)
- [x] `hive_groups` and `hive_group_members` tables for group management
- [x] `hive_feedback_events` table for cross-agent feedback
- [x] `agent_registry` table (replaces YAML file):
  - `id` (PK), `name`, `profile`, `skills` (JSONB), `project_root`, `groups` (JSONB)
  - `registered_at`, `last_seen_at` timestamps
- [x] GIN index on `tags` for `@>` containment queries
- [x] GIN index on `search_vector` for full-text search
- [x] IVFFlat or HNSW index on `embedding` via pgvector
- [x] Composite index on `(namespace, confidence)` for filtered scans
- [x] Schema version tracking table (`hive_schema_version`)
- [x] SQL migration files in `src/tapps_brain/migrations/hive/` (numbered, forward-only)

---

### STORY-055.2: Connection management and pooling

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-055.1
**Context refs:** `src/tapps_brain/sqlcipher_util.py` (existing SQLite connection patterns)
**Verification:** `pytest tests/unit/test_postgres_connection.py -v --tb=short -m "not benchmark"`

#### Why

SQLite connections are trivial (open file). Postgres connections are expensive (~100ms handshake). A connection pool amortizes this cost. The pool must be synchronous (no asyncpg) to match tapps-brain's sync-only core.

#### Acceptance Criteria

- [x] `psycopg` (sync) with `psycopg_pool.ConnectionPool` for connection management
- [x] `PostgresConnectionManager` class:
  - Constructor takes DSN string + pool config (min_size, max_size, timeout)
  - `get_connection()` context manager for transactional operations
  - `close()` for graceful shutdown
- [x] Environment variables:
  - `TAPPS_BRAIN_HIVE_DSN` — Postgres connection string
  - `TAPPS_BRAIN_HIVE_POOL_MIN` — minimum pool connections (default 2)
  - `TAPPS_BRAIN_HIVE_POOL_MAX` — maximum pool connections (default 10)
  - `TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT` — connection timeout seconds (default 5)
- [x] SSL support via DSN parameters (`sslmode=require`)
- [x] Connection health checking (pool validates before lending)
- [x] Graceful behavior on pool exhaustion (wait with timeout, then raise)

---

### STORY-055.3: PostgresHiveBackend — core CRUD operations

**Status:** done (2026-04-09)
**Effort:** L
**Depends on:** STORY-055.1, STORY-055.2
**Context refs:** `src/tapps_brain/hive.py` (`HiveStore.save`, `get`, `search`, `patch_confidence`)
**Verification:** `pytest tests/unit/test_postgres_hive_backend.py -v --tb=short -m "not benchmark"`

#### Why

This is the core implementation — save, get, search, and confidence updates against Postgres. All other features build on top of these primitives.

#### Acceptance Criteria

- [x] `PostgresHiveBackend` satisfies `HiveBackend` protocol
- [x] `save()` — INSERT with ON CONFLICT (namespace, key) upsert; applies `conflict_policy`
- [x] `get()` — SELECT by key + namespace
- [x] `search()` — hybrid query combining:
  - `tsvector @@ plainto_tsquery()` for full-text relevance
  - `embedding <-> query_embedding` for semantic similarity (pgvector)
  - `confidence >= min_confidence` filter
  - Results ranked by composite score (same weights as SQLite backend)
- [x] `patch_confidence()` — UPDATE confidence with optimistic concurrency
- [x] `get_confidence()` — SELECT confidence by namespace + key
- [x] All operations use connection pool (no raw connection creation)
- [x] Parameterized queries throughout (no SQL injection)
- [x] Behavioral parity with `SqliteHiveBackend` — same test suite passes against both

---

### STORY-055.4: PostgresHiveBackend — group operations

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-055.3
**Context refs:** `src/tapps_brain/hive.py` (group methods)
**Verification:** `pytest tests/unit/test_postgres_hive_groups.py -v --tb=short -m "not benchmark"`

#### Why

Groups are the mechanism for workflow-scoped sharing (dev-pipeline, frontend-guild). These operations manage group lifecycle and membership, and power `search_with_groups()` which merges group-scoped memories into recall.

#### Acceptance Criteria

- [x] `create_group()`, `add_group_member()`, `remove_group_member()` — standard CRUD
- [x] `list_groups()`, `get_group_members()`, `get_agent_groups()` — read operations
- [x] `agent_is_group_member()` — membership check
- [x] `search_with_groups()` — search across all groups the agent belongs to, merged with namespace results
- [x] Group membership is many-to-many (agent can be in multiple groups)
- [x] Foreign key from `hive_group_members` to `agent_registry`
- [x] Cascading behavior: unregistering an agent removes group memberships

---

### STORY-055.5: PostgresHiveBackend — feedback and notifications

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-055.3
**Context refs:** `src/tapps_brain/hive.py` (feedback_event methods, write_notify)
**Verification:** `pytest tests/unit/test_postgres_hive_feedback.py -v --tb=short -m "not benchmark"`

#### Why

Feedback events track cross-agent quality signals. Write notifications enable agents to detect when new shared knowledge is available. Postgres `LISTEN/NOTIFY` is a natural fit, replacing the file-based polling mechanism.

#### Acceptance Criteria

- [x] `record_feedback_event()` — INSERT into `hive_feedback_events`
- [x] `query_feedback_events()` — SELECT with namespace/key filters
- [x] `get_write_notify_state()` — returns current notification sequence number
- [x] `wait_for_write_notify()` — uses Postgres `LISTEN` channel instead of file polling:
  - `NOTIFY hive_writes` fired by trigger on `hive_memories` INSERT/UPDATE
  - `LISTEN hive_writes` with timeout for waiting callers
  - Falls back to polling if LISTEN is not available (connection pool constraints)
- [x] Notification payload includes namespace and key for selective wake-up

---

### STORY-055.6: PostgresHiveBackend — agent registry in Postgres

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-055.3
**Context refs:** `src/tapps_brain/hive.py` (`AgentRegistry`, `AgentRegistration`)
**Verification:** `pytest tests/unit/test_postgres_agent_registry.py -v --tb=short -m "not benchmark"`

#### Why

The YAML-based `AgentRegistry` at `~/.tapps-brain/hive/agents.yaml` is a single-host artifact. With Postgres backing the Hive, the agent registry should live in the same database — queryable, transactional, and accessible from any host.

#### Acceptance Criteria

- [x] `PostgresAgentRegistry` satisfies `AgentRegistryBackend` protocol
- [x] `register()` — UPSERT into `agent_registry` table
- [x] `unregister()` — DELETE with cascading group membership cleanup
- [x] `get()` — SELECT by agent_id
- [x] `list_agents()` — SELECT all with optional profile/project filters
- [x] `agents_for_domain()` — SELECT WHERE profile = domain
- [x] `last_seen_at` updated on every `register()` or store access (heartbeat)
- [x] Migration from YAML: CLI command to import existing `agents.yaml` into Postgres table

---

### STORY-055.7: PostgresFederationBackend

**Status:** done (2026-04-09)
**Effort:** L
**Depends on:** STORY-055.2
**Context refs:** `src/tapps_brain/federation.py` (full public API)
**Verification:** `pytest tests/unit/test_postgres_federation_backend.py -v --tb=short -m "not benchmark"`

#### Why

Federation enables cross-project knowledge sharing. With Postgres, projects on different hosts can publish and subscribe to a central hub without needing shared filesystem access.

#### Acceptance Criteria

- [x] `PostgresFederationBackend` satisfies `FederationBackend` protocol
- [x] `federated_memories` table with `project_id`, entries, `tsvector`, `vector(384)`
- [x] `federation_subscriptions` table (replaces YAML config)
- [x] `publish()` — bulk INSERT with ON CONFLICT handling
- [x] `unpublish()` — DELETE by project_id + keys
- [x] `search()` — hybrid tsvector + pgvector search with project/tag/confidence filters
- [x] `get_project_entries()`, `get_stats()` — read operations
- [x] `sync_to_hub()` and `sync_from_hub()` work with Postgres backend
- [x] Schema migration files in `src/tapps_brain/migrations/federation/`

---

### STORY-055.8: Backend conformance test suite

**Status:** done (2026-04-09)
**Effort:** L
**Depends on:** STORY-055.3, STORY-055.4, STORY-055.5, STORY-055.6, STORY-055.7
**Context refs:** `tests/unit/test_hive.py`, `tests/unit/test_federation.py`
**Verification:** `pytest tests/integration/test_backend_conformance.py -v --tb=short -m "not benchmark"`

#### Why

Both SQLite and Postgres backends must produce identical behavior. A shared conformance test suite parameterized by backend ensures parity and catches divergence as features are added.

#### Acceptance Criteria

- [x] `tests/integration/test_backend_conformance.py` with pytest parametrize over `[SqliteHiveBackend, PostgresHiveBackend]`
- [x] Covers: save/get/search, groups, feedback, notifications, agent registry
- [x] Federation conformance: `[SqliteFederationBackend, PostgresFederationBackend]`
- [x] Postgres tests require `TAPPS_TEST_POSTGRES_DSN` env var (skipped if unset — CI provides it)
- [x] SQLite tests always run (no external dependency)
- [x] Test fixtures handle schema creation and teardown per test
- [x] Semantic search parity: same query returns same top-k results (order may vary within same score)

---

### STORY-055.9: Schema migration tooling

**Status:** done (2026-04-09)
**Effort:** M
**Depends on:** STORY-055.1
**Context refs:** `src/tapps_brain/persistence.py` (existing SQLite migration pattern)
**Verification:** `pytest tests/unit/test_postgres_migrations.py -v --tb=short -m "not benchmark"`

#### Why

SQLite schema migrations are embedded in `persistence.py` as inline `ALTER TABLE` statements. Postgres needs proper migration files — versioned, forward-only, and runnable by CI/CD. This tooling ensures schema changes are safe and auditable.

#### Acceptance Criteria

- [x] Migration files: `src/tapps_brain/migrations/hive/001_initial.sql`, `002_*.sql`, etc.
- [x] Migration runner: `apply_hive_migrations(dsn)` — reads current version, applies pending migrations in order
- [x] Version tracking: `hive_schema_version` table with `version`, `applied_at`, `checksum`
- [x] CLI command: `tapps-brain maintenance migrate-hive` (applies pending migrations)
- [x] CLI command: `tapps-brain maintenance hive-schema-status` (shows current version and pending)
- [x] Dry-run support: `--dry-run` shows SQL without executing
- [x] Migrations are idempotent (re-running a completed migration is a no-op)
- [x] Federation migrations follow the same pattern in `src/tapps_brain/migrations/federation/`

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | STORY-055.1 | Schema design drives everything |
| 2 | STORY-055.2, STORY-055.9 | Connection pool + migrations (can parallelize) |
| 3 | STORY-055.3 | Core CRUD — the minimum viable backend |
| 4 | STORY-055.4, STORY-055.5, STORY-055.6 | Feature parity (can parallelize) |
| 5 | STORY-055.7 | Federation backend |
| 6 | STORY-055.8 | Conformance tests validate everything |
