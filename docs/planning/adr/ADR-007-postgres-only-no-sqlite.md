# ADR-007: PostgreSQL-only persistence plane (SQLite fully removed)

## Status

Accepted (2026-04-10)
Extended (2026-04-11) — scope widened from "shared stores only" to **all persistence**: private agent memory, vector ANN, lexical FTS, and at-rest encryption now live entirely in Postgres. SQLite is no longer a build or runtime dependency.

## Context

`tapps-brain` historically offered SQLite implementations for Hive (`HiveStore`, `SqliteHiveBackend`) and Federation (`SqliteFederationBackend`) so developers could run without Postgres. In parallel, agent-local memory ran on a per-project `memory.db` (SQLite + FTS5 + `sqlite-vec` ANN + optional SQLCipher encryption). That split operational models, encouraged split-brain between laptops and shared infrastructure, duplicated test matrices, and left two engines to audit for every security or backup review.

Greenfield v3 policy: **SQLite does not exist anywhere in the product surface** — not for shared stores, not for private memory, not for vector indexes, not for at-rest encryption.

## Decision

1. **`create_hive_backend()`, `create_federation_backend()`, `create_private_backend()`** accept **only** `postgres://` or `postgresql://` DSNs. Non-Postgres arguments raise `ValueError`.
2. **`SqliteHiveBackend`, `SqliteFederationBackend`, `MemoryPersistence` (SQLite private memory), `SqliteVecIndex`, `sqlcipher_util`, `SqliteAgentRegistryBackend`** are deleted from the tree. No re-exports, no shims, no back-compat path.
3. **Private memory** runs on `PostgresPrivateBackend` against the `private_memories` table (see `src/tapps_brain/migrations/private/001_initial.sql`). Every row is keyed by `(project_id, agent_id, key)` — file paths are no longer a trust boundary.
4. **Vector ANN index** is **pgvector HNSW** (`m=16, ef_construction=200`, `vector_cosine_ops`) — see migration `002_hnsw_upgrade.sql`. IVFFlat is not used. `sqlite-vec` is removed as a dependency.
5. **Lexical retrieval** is Postgres `tsvector` + GIN via the BEFORE INSERT/UPDATE trigger in `001_initial.sql` (A/B/C weighting on `key` / `value` / `tags`). Option to upgrade to ParadeDB `pg_search` (BM25 on Tantivy) when ranking quality becomes the bottleneck — tracked as a follow-up, not blocking.
6. **At-rest encryption** is Percona **`pg_tde`** 2.1.2+ (production WAL encryption, released 2026-03-02) or the cloud provider's TDE. SQLCipher and the `encryption` extra are removed.
7. **CI** runs the full suite against an ephemeral Postgres (pgvector) service via `TAPPS_TEST_POSTGRES_DSN`. Startup fails fast when the DSN is missing — there is no silent fallback.
8. **Pre-GA greenfield:** no migration tool, no v2 import path, no "legacy SQLite mode." Users start with Postgres or they do not start.

## Consequences

- **Local dev** requires Docker Compose (or a reachable Postgres) for *any* `tapps-brain` invocation. The `make brain-up` target brings up pgvector/pg17 in one command.
- **Dependency surface shrinks**: `sqlite-vec`, `pysqlcipher3`, and the `encryption` extra are gone. `psycopg[binary,pool]` replaces them.
- **Test matrix collapses** to one engine. SQLite-only tests (`test_memory_persistence`, `test_persistence_sqlite_vec`, `test_sqlite_vec_*`, `test_sqlcipher_*`, `test_sqlite_corruption`) are deleted.
- **Docs surface shrinks**: `docs/guides/sqlite-*.md` and `docs/guides/sqlcipher*.md` are deleted; `docs/engineering/*` is drift-swept to remove SQLite references.
- **Security review** is one engine, one backup story, one encryption extension, one access-control model.
- **Vector search** gains HNSW's write-tolerance (no rebuild-after-bulk-load) and ~1.5× query speedup at comparable recall vs the previous IVFFlat default.

## Supersedes / updates

- Supersedes the narrower **ADR-007 (2026-04-10)** decision that kept private-memory SQLite in place.
- Supersedes **ADR-004** (scale-out single-node SQLite defer) — no code path uses SQLite.
- Closes the EPIC-059 acceptance criterion: *"No supported runtime path uses SQLite for Hive, Federation, or private agent memory."*

## 2026 research notes

- **pgvector HNSW vs IVFFlat** — AWS / Google Cloud / pgvector project guidance converges on HNSW as the default for RAG and semantic-recall workloads in 2026; IVFFlat retains an edge only at 50M+ rows with memory/build-time pressure.
- **Postgres `tsvector` vs ParadeDB `pg_search`** — `tsvector` is adequate for short-document lexical recall with A/B/C weighting; `pg_search` (Tantivy/BM25) is the upgrade path when IDF-aware ranking matters. Noted: Neon stopped offering `pg_search` for new projects as of 2026-03-19 (existing projects unaffected) — relevant for Neon-hosted deployments.
- **Percona `pg_tde` 2.1.2** (2026-03-02) — first open-source Postgres TDE with production-ready WAL encryption, Vault/OpenBao key storage, bundled with Percona Distribution for PostgreSQL 17+.
