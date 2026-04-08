---
id: EPIC-055
title: "PostgreSQL Hive & Federation Backend"
status: planned
priority: high
created: 2026-04-08
tags: [postgres, hive, federation, pgvector, scaling, multi-host]
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

- [ ] `create_hive_backend("postgres://...")` returns a working `PostgresHiveBackend`
- [ ] All `HiveBackend` protocol methods implemented and passing protocol conformance tests
- [ ] `pgvector` 384-dim index for semantic search across Hive memories
- [ ] `tsvector` GIN index for full-text search
- [ ] `LISTEN/NOTIFY` for real-time write notifications (replacing file polling)
- [ ] Connection pooling with configurable min/max connections
- [ ] Schema migration tooling (versioned, forward-only)
- [ ] All existing Hive behavioral tests pass against Postgres backend
- [ ] Federation backend implemented with same coverage
- [ ] Graceful fallback: if Postgres is unreachable, agent local memory still works

## Stories

### STORY-055.1: PostgreSQL schema design for Hive

**Status:** planned
**Effort:** L
**Depends on:** EPIC-054 (protocols defined)
**Context refs:** `src/tapps_brain/hive.py` (SQLite schema in `_ensure_schema`), `src/tapps_brain/persistence.py` (local schema for reference)
**Verification:** `pytest tests/unit/test_postgres_hive_schema.py -v --tb=short -m "not benchmark"`

#### Why

The Hive SQLite schema uses FTS5 triggers, `json_each()` for tag queries, and custom collation. Postgres has different capabilities (native JSONB, GIN indexes, tsvector). The schema must be designed for Postgres idioms, not ported line-by-line.

#### Acceptance Criteria

- [ ] `hive_memories` table with columns matching `HiveStore.save()` parameters:
  - `namespace`, `key`, `value`, `source_agent`, `tier`, `confidence`, `source`
  - `tags` as `JSONB` (not TEXT) for native array queries
  - `valid_at`, `invalid_at`, `superseded_by` (temporal)
  - `memory_group`, `conflict_policy`
  - `embedding` as `vector(384)` via pgvector
  - `created_at`, `updated_at` timestamps with timezone
  - `search_vector` as `tsvector` (auto-updated via trigger)
- [ ] `hive_groups` and `hive_group_members` tables for group management
- [ ] `hive_feedback_events` table for cross-agent feedback
- [ ] `agent_registry` table (replaces YAML file):
  - `id` (PK), `name`, `profile`, `skills` (JSONB), `project_root`, `groups` (JSONB)
  - `registered_at`, `last_seen_at` timestamps
- [ ] GIN index on `tags` for `@>` containment queries
- [ ] GIN index on `search_vector` for full-text search
- [ ] IVFFlat or HNSW index on `embedding` via pgvector
- [ ] Composite index on `(namespace, confidence)` for filtered scans
- [ ] Schema version tracking table (`hive_schema_version`)
- [ ] SQL migration files in `src/tapps_brain/migrations/hive/` (numbered, forward-only)

---

### STORY-055.2: Connection management and pooling

**Status:** planned
**Effort:** M
**Depends on:** STORY-055.1
**Context refs:** `src/tapps_brain/sqlcipher_util.py` (existing SQLite connection patterns)
**Verification:** `pytest tests/unit/test_postgres_connection.py -v --tb=short -m "not benchmark"`

#### Why

SQLite connections are trivial (open file). Postgres connections are expensive (~100ms handshake). A connection pool amortizes this cost. The pool must be synchronous (no asyncpg) to match tapps-brain's sync-only core.

#### Acceptance Criteria

- [ ] `psycopg` (sync) with `psycopg_pool.ConnectionPool` for connection management
- [ ] `PostgresConnectionManager` class:
  - Constructor takes DSN string + pool config (min_size, max_size, timeout)
  - `get_connection()` context manager for transactional operations
  - `close()` for graceful shutdown
- [ ] Environment variables:
  - `TAPPS_BRAIN_HIVE_DSN` — Postgres connection string
  - `TAPPS_BRAIN_HIVE_POOL_MIN` — minimum pool connections (default 2)
  - `TAPPS_BRAIN_HIVE_POOL_MAX` — maximum pool connections (default 10)
  - `TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT` — connection timeout seconds (default 5)
- [ ] SSL support via DSN parameters (`sslmode=require`)
- [ ] Connection health checking (pool validates before lending)
- [ ] Graceful behavior on pool exhaustion (wait with timeout, then raise)

---

### STORY-055.3: PostgresHiveBackend — core CRUD operations

**Status:** planned
**Effort:** L
**Depends on:** STORY-055.1, STORY-055.2
**Context refs:** `src/tapps_brain/hive.py` (`HiveStore.save`, `get`, `search`, `patch_confidence`)
**Verification:** `pytest tests/unit/test_postgres_hive_backend.py -v --tb=short -m "not benchmark"`

#### Why

This is the core implementation — save, get, search, and confidence updates against Postgres. All other features build on top of these primitives.

#### Acceptance Criteria

- [ ] `PostgresHiveBackend` satisfies `HiveBackend` protocol
- [ ] `save()` — INSERT with ON CONFLICT (namespace, key) upsert; applies `conflict_policy`
- [ ] `get()` — SELECT by key + namespace
- [ ] `search()` — hybrid query combining:
  - `tsvector @@ plainto_tsquery()` for full-text relevance
  - `embedding <-> query_embedding` for semantic similarity (pgvector)
  - `confidence >= min_confidence` filter
  - Results ranked by composite score (same weights as SQLite backend)
- [ ] `patch_confidence()` — UPDATE confidence with optimistic concurrency
- [ ] `get_confidence()` — SELECT confidence by namespace + key
- [ ] All operations use connection pool (no raw connection creation)
- [ ] Parameterized queries throughout (no SQL injection)
- [ ] Behavioral parity with `SqliteHiveBackend` — same test suite passes against both

---

### STORY-055.4: PostgresHiveBackend — group operations

**Status:** planned
**Effort:** M
**Depends on:** STORY-055.3
**Context refs:** `src/tapps_brain/hive.py` (group methods)
**Verification:** `pytest tests/unit/test_postgres_hive_groups.py -v --tb=short -m "not benchmark"`

#### Why

Groups are the mechanism for workflow-scoped sharing (dev-pipeline, frontend-guild). These operations manage group lifecycle and membership, and power `search_with_groups()` which merges group-scoped memories into recall.

#### Acceptance Criteria

- [ ] `create_group()`, `add_group_member()`, `remove_group_member()` — standard CRUD
- [ ] `list_groups()`, `get_group_members()`, `get_agent_groups()` — read operations
- [ ] `agent_is_group_member()` — membership check
- [ ] `search_with_groups()` — search across all groups the agent belongs to, merged with namespace results
- [ ] Group membership is many-to-many (agent can be in multiple groups)
- [ ] Foreign key from `hive_group_members` to `agent_registry`
- [ ] Cascading behavior: unregistering an agent removes group memberships

---

### STORY-055.5: PostgresHiveBackend — feedback and notifications

**Status:** planned
**Effort:** M
**Depends on:** STORY-055.3
**Context refs:** `src/tapps_brain/hive.py` (feedback_event methods, write_notify)
**Verification:** `pytest tests/unit/test_postgres_hive_feedback.py -v --tb=short -m "not benchmark"`

#### Why

Feedback events track cross-agent quality signals. Write notifications enable agents to detect when new shared knowledge is available. Postgres `LISTEN/NOTIFY` is a natural fit, replacing the file-based polling mechanism.

#### Acceptance Criteria

- [ ] `record_feedback_event()` — INSERT into `hive_feedback_events`
- [ ] `query_feedback_events()` — SELECT with namespace/key filters
- [ ] `get_write_notify_state()` — returns current notification sequence number
- [ ] `wait_for_write_notify()` — uses Postgres `LISTEN` channel instead of file polling:
  - `NOTIFY hive_writes` fired by trigger on `hive_memories` INSERT/UPDATE
  - `LISTEN hive_writes` with timeout for waiting callers
  - Falls back to polling if LISTEN is not available (connection pool constraints)
- [ ] Notification payload includes namespace and key for selective wake-up

---

### STORY-055.6: PostgresHiveBackend — agent registry in Postgres

**Status:** planned
**Effort:** M
**Depends on:** STORY-055.3
**Context refs:** `src/tapps_brain/hive.py` (`AgentRegistry`, `AgentRegistration`)
**Verification:** `pytest tests/unit/test_postgres_agent_registry.py -v --tb=short -m "not benchmark"`

#### Why

The YAML-based `AgentRegistry` at `~/.tapps-brain/hive/agents.yaml` is a single-host artifact. With Postgres backing the Hive, the agent registry should live in the same database — queryable, transactional, and accessible from any host.

#### Acceptance Criteria

- [ ] `PostgresAgentRegistry` satisfies `AgentRegistryBackend` protocol
- [ ] `register()` — UPSERT into `agent_registry` table
- [ ] `unregister()` — DELETE with cascading group membership cleanup
- [ ] `get()` — SELECT by agent_id
- [ ] `list_agents()` — SELECT all with optional profile/project filters
- [ ] `agents_for_domain()` — SELECT WHERE profile = domain
- [ ] `last_seen_at` updated on every `register()` or store access (heartbeat)
- [ ] Migration from YAML: CLI command to import existing `agents.yaml` into Postgres table

---

### STORY-055.7: PostgresFederationBackend

**Status:** planned
**Effort:** L
**Depends on:** STORY-055.2
**Context refs:** `src/tapps_brain/federation.py` (full public API)
**Verification:** `pytest tests/unit/test_postgres_federation_backend.py -v --tb=short -m "not benchmark"`

#### Why

Federation enables cross-project knowledge sharing. With Postgres, projects on different hosts can publish and subscribe to a central hub without needing shared filesystem access.

#### Acceptance Criteria

- [ ] `PostgresFederationBackend` satisfies `FederationBackend` protocol
- [ ] `federated_memories` table with `project_id`, entries, `tsvector`, `vector(384)`
- [ ] `federation_subscriptions` table (replaces YAML config)
- [ ] `publish()` — bulk INSERT with ON CONFLICT handling
- [ ] `unpublish()` — DELETE by project_id + keys
- [ ] `search()` — hybrid tsvector + pgvector search with project/tag/confidence filters
- [ ] `get_project_entries()`, `get_stats()` — read operations
- [ ] `sync_to_hub()` and `sync_from_hub()` work with Postgres backend
- [ ] Schema migration files in `src/tapps_brain/migrations/federation/`

---

### STORY-055.8: Backend conformance test suite

**Status:** planned
**Effort:** L
**Depends on:** STORY-055.3, STORY-055.4, STORY-055.5, STORY-055.6, STORY-055.7
**Context refs:** `tests/unit/test_hive.py`, `tests/unit/test_federation.py`
**Verification:** `pytest tests/integration/test_backend_conformance.py -v --tb=short -m "not benchmark"`

#### Why

Both SQLite and Postgres backends must produce identical behavior. A shared conformance test suite parameterized by backend ensures parity and catches divergence as features are added.

#### Acceptance Criteria

- [ ] `tests/integration/test_backend_conformance.py` with pytest parametrize over `[SqliteHiveBackend, PostgresHiveBackend]`
- [ ] Covers: save/get/search, groups, feedback, notifications, agent registry
- [ ] Federation conformance: `[SqliteFederationBackend, PostgresFederationBackend]`
- [ ] Postgres tests require `TAPPS_TEST_POSTGRES_DSN` env var (skipped if unset — CI provides it)
- [ ] SQLite tests always run (no external dependency)
- [ ] Test fixtures handle schema creation and teardown per test
- [ ] Semantic search parity: same query returns same top-k results (order may vary within same score)

---

### STORY-055.9: Schema migration tooling

**Status:** planned
**Effort:** M
**Depends on:** STORY-055.1
**Context refs:** `src/tapps_brain/persistence.py` (existing SQLite migration pattern)
**Verification:** `pytest tests/unit/test_postgres_migrations.py -v --tb=short -m "not benchmark"`

#### Why

SQLite schema migrations are embedded in `persistence.py` as inline `ALTER TABLE` statements. Postgres needs proper migration files — versioned, forward-only, and runnable by CI/CD. This tooling ensures schema changes are safe and auditable.

#### Acceptance Criteria

- [ ] Migration files: `src/tapps_brain/migrations/hive/001_initial.sql`, `002_*.sql`, etc.
- [ ] Migration runner: `apply_hive_migrations(dsn)` — reads current version, applies pending migrations in order
- [ ] Version tracking: `hive_schema_version` table with `version`, `applied_at`, `checksum`
- [ ] CLI command: `tapps-brain maintenance migrate-hive` (applies pending migrations)
- [ ] CLI command: `tapps-brain maintenance hive-schema-status` (shows current version and pending)
- [ ] Dry-run support: `--dry-run` shows SQL without executing
- [ ] Migrations are idempotent (re-running a completed migration is a no-op)
- [ ] Federation migrations follow the same pattern in `src/tapps_brain/migrations/federation/`

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | STORY-055.1 | Schema design drives everything |
| 2 | STORY-055.2, STORY-055.9 | Connection pool + migrations (can parallelize) |
| 3 | STORY-055.3 | Core CRUD — the minimum viable backend |
| 4 | STORY-055.4, STORY-055.5, STORY-055.6 | Feature parity (can parallelize) |
| 5 | STORY-055.7 | Federation backend |
| 6 | STORY-055.8 | Conformance tests validate everything |
