# ADR-007: PostgreSQL-only Hive and Federation backends (SQLite removed from factories)

## Status

Accepted (2026-04-10)

## Context

`tapps-brain` historically offered SQLite implementations for Hive (`HiveStore`, `SqliteHiveBackend`) and Federation (`SqliteFederationBackend`) so developers could run without Postgres. That split operational models, encouraged split-brain between laptops and shared infrastructure, and duplicated test matrices.

Greenfield v3 policy: **SQLite does not exist for shared stores** from the public API perspective.

## Decision

1. **`create_hive_backend()` and `create_federation_backend()`** accept **only** `postgres://` or `postgresql://` DSNs. Non-Postgres arguments raise `ValueError` with an explicit message.
2. **`SqliteHiveBackend` and `SqliteFederationBackend` are removed** from `backends.py` and from package exports.
3. **CI** runs with a **PostgreSQL (pgvector) service** and sets `TAPPS_TEST_POSTGRES_DSN` so integration/conformance tests exercise real Postgres.
4. **Agent-local memory** (`MemoryPersistence` / `memory.db`) **still uses SQLite** until EPIC-059 STORY-059.2 lands a Postgres-backed private store—at which point SQLite is removed there too. Until then, ADR-004’s single-node SQLite note applies only to **private** memory, not Hive/Federation.

## Consequences

- **Local dev** requires Docker Compose or a reachable Postgres for any code path that constructs Hive/Federation backends via the factories.
- **Tests** that relied on file-backed Hive/Federation must use `TAPPS_TEST_POSTGRES_DSN` (provided in CI) or mocks.
- **Imports** of `SqliteHiveBackend` / `SqliteFederationBackend` break; update to `PostgresHiveBackend` / `PostgresFederationBackend` via `create_*_backend(dsn)`.

## Supersedes / updates

- Complements **EPIC-059** (Postgres-only persistence plane).
- Narrows **ADR-004** (scale-out): shared stores are no longer “single-node SQLite defer”; they are Postgres-only.
