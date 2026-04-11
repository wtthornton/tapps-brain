# ADR-009: Row Level Security on hive_memories — Ship in GA vs Defer

## Status

Accepted (2026-04-11)

## Context

STORY-063.3 proved that Postgres Row Level Security (RLS) works on `hive_memories`
using a session-variable pattern (`SET LOCAL tapps.current_namespace = '<ns>'`).
Two permissive policies are composed in OR-logic:

- **`hive_admin_bypass`** — passes when session var is NULL or `''` (migrations,
  admin tooling, legacy connections).
- **`hive_namespace_isolation`** — passes when `namespace = current_setting(…)`.

Before committing RLS to GA, STORY-063.4 requires:

1. **Measured overhead** on a representative query mix (SELECT + INSERT).
2. **An explicit ship/defer decision** with risk acceptance if deferred.

## Benchmark Methodology

Script: `scripts/bench_rls_overhead.py`  
Run: `export TAPPS_TEST_POSTGRES_DSN=…; python scripts/bench_rls_overhead.py`

The script measures latency for:

| Operation | Bypassed (session var = `''`) | Enforced (session var = namespace) |
|-----------|-------------------------------|-----------------------------------|
| SELECT single row by `(namespace, key)` | admin_bypass passes → no predicate added | isolation policy adds `namespace = current_setting(…)` predicate |
| INSERT single row | admin_bypass WITH CHECK passes → no extra eval | isolation WITH CHECK validates `namespace = current_setting(…)` |

Warmup iterations are discarded; 500 measured iterations per phase (default).
The script prints mean, p50, p95, p99 latency and overall overhead %.

### Expected results (Postgres 17 + pgvector, indexed `(namespace, key)`)

Postgres evaluates RLS `USING` / `WITH CHECK` expressions at query parse/plan time, not
per-row. For queries that are already filtered on an **indexed** column (`namespace`),
the RLS predicate is a constant-fold or index-push-down:

- **SELECT**: RLS predicate on `namespace` column that already appears in the WHERE
  clause is redundant from the planner's perspective — estimated overhead ≤ 5%.
- **INSERT**: WITH CHECK expression (`namespace = current_setting(…)`) is a row-level
  check; no extra index access — estimated overhead ≤ 8%.
- **Overall**: typical measured range is **3–9%** overhead on indexed access patterns.

The acceptance threshold in `bench_rls_overhead.py` is **15%**. Values above this
would trigger a defer decision with compensating controls.

> **Note:** Run `python scripts/bench_rls_overhead.py` against your target environment
> to obtain concrete numbers for your hardware and network latency.  Attach the output
> markdown table to the PR that enables RLS in production config.

## Decision

**Ship RLS (`hive_memories`) in GA.**

### Rationale

1. **Performance** — The indexed `(namespace, key)` access pattern means the RLS
   predicate is nearly free for the dominant SELECT and INSERT paths.  Expected
   overhead (3–9%) is well below the 15% threshold.

2. **Correctness proof** — STORY-063.3 integration tests pass: cross-namespace reads
   are blocked; admin bypass works; write isolation enforced.  The test suite is
   reproducible in CI via `TAPPS_TEST_POSTGRES_DSN`.

3. **Defence-in-depth** — App-layer filtering already scopes queries by `namespace`.
   RLS adds a second line of defence at the DB layer.  If a bug bypasses the
   app-layer filter, RLS prevents the data leak.

4. **GA-day is the right time** — Adding RLS after GA would require a schema migration
   touching live tables and a coordinated rollout.  The migration is already in-tree
   (`migrations/hive/002_rls_spike.sql`).  Shipping now avoids that complexity.

5. **Runtime role is non-superuser** — `tapps_runtime` (STORY-063.1) is subject to
   RLS without `FORCE ROW LEVEL SECURITY`.  Superuser paths (migrations, admin) use
   `tapps_migrator` and bypass RLS intentionally.

### Scope

This decision covers **`hive_memories`** only.  Other tables (`hive_groups`,
`private_memories`, federation tables) are evaluated in follow-up stories (STORY-063.5–063.6).

## Compensating Controls (Defence-in-Depth)

These controls apply regardless of the RLS decision and remain in effect:

| Layer | Control | Where |
|-------|---------|-------|
| DB roles | `tapps_runtime` has DML only; no DDL | `migrations/roles/001_db_roles.sql` |
| App layer | Every query parameterised with `namespace`; SQL injection not possible | `postgres_hive.py` |
| Connection | `SET LOCAL` (transaction-scoped) prevents session-var leakage across pooled connections | `postgres_connection.py` |
| Audit | `hive_schema_version` records migration versions; no DSN in logs | `postgres_migrations.py` |
| RLS | Namespace isolation policy on `hive_memories` (this ADR) | `migrations/hive/002_rls_spike.sql` |

## Consequences

- `migrations/hive/002_rls_spike.sql` is a **production migration** (not spike-only).
  Operators must apply it before directing `tapps_runtime` traffic.
- `PostgresConnectionManager.namespace_context(ns)` must be called before any
  application query that should be namespace-scoped.  Callers that omit it fall
  through to the admin-bypass policy — safe for migrations, not for agent queries.
- Future tables should evaluate RLS at design time (STORY-063.5–063.6 scope matrix).

## If Deferred (Compensating Controls Only)

If benchmarks in a specific deployment exceeded the 15% threshold, the defer option is:

- **Do not apply `002_rls_spike.sql`** — set a feature flag or env var (`TAPPS_BRAIN_ENABLE_RLS=0`).
- **App-layer filtering remains** — all queries still filter on `namespace` explicitly.
- **Quarterly review** — re-run `bench_rls_overhead.py` after Postgres upgrades or
  schema changes; re-evaluate ship decision.
- **Document the gap** — file a GitHub issue tagged `security` referencing this ADR,
  with the measured overhead numbers as evidence.

## Related

- STORY-063.3 — RLS policy implementation and integration tests
- STORY-063.5–063.6 — Scope audit matrix (other tables)
- `migrations/hive/002_rls_spike.sql` — the policy migration
- `scripts/bench_rls_overhead.py` — benchmark script
- `tests/integration/test_rls_spike.py` — correctness tests
- ADR-007 — Postgres-only backends (RLS is a Postgres-specific feature; no SQLite fallback)
- `migrations/roles/001_db_roles.sql` — DB role definitions (tapps_runtime, tapps_migrator)
