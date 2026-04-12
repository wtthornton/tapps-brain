# v3 Behavioral Parity — What Changed vs v2

**Epic:** [EPIC-059](../planning/epics/EPIC-059.md) — Greenfield v3 Postgres-Only Persistence Plane  
**Decision record:** [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)  
**Status:** v3 greenfield (no migration path from v2)

---

## Summary

v3 is a **greenfield** redesign. The retrieval, decay, consolidation, and safety logic are
**unchanged** from v2. The storage layer is the only major change: PostgreSQL replaces SQLite
everywhere (private memory, Hive, Federation). No silent fallback to SQLite exists in v3.

---

## What Is Unchanged (v2 parity)

### Exponential Decay

| Parameter | Value |
|-----------|-------|
| Model | Exponential (`confidence × e^(-λt)`) |
| Evaluation | Lazy on read — no background threads |
| `architectural` half-life | 180 days |
| `pattern` half-life | 60 days |
| `procedural` half-life | 30 days |
| `context` half-life | 14 days |
| `ephemeral` half-life | 1 day |
| `session` half-life | 7 days |
| Stale threshold | 0.3 confidence |

Optional FSRS-lite (`adaptive_stability`) is available when a profile enables it — behaviour
is identical to v2 (profile flag `adaptive_stability: true`). See `docs/guides/memory-decay-and-fsrs.md`.

### Consolidation

- **Algorithm:** Jaccard + TF-IDF similarity (no LLM calls, fully deterministic).
- **Trigger:** Auto-consolidation on save when `ConsolidationConfig.enabled = True` (profile or constructor).
- **Merge policy:** Newest value wins for key collisions; provenance tracked in `ConsolidatedEntry.source_keys`.
- **Undo:** `MemoryStore.undo_consolidation_merge` + CLI `maintenance consolidation-merge-undo` — unchanged.
- **Audit log:** JSONL at `{store_dir}/memory/memory_log.jsonl` — see note below on v3 status.

### Safety

- **Prompt injection detection** patterns unchanged (ruleset version `1.0.0`).
- **Block vs sanitize** decision logic unchanged.
- All content still passes through `ContentSafetyFilter` before storage or retrieval injection.
- `MetricsCollector` increments for block/sanitize outcomes still supported when wired.

### Retrieval Scoring

Composite scoring formula unchanged:

```
score = 0.40 × relevance + 0.30 × confidence + 0.15 × recency + 0.15 × frequency
```

BM25 (Okapi, pure Python) and Reciprocal Rank Fusion (hybrid BM25 + vector) are unchanged.
Profile-tunable `hybrid_fusion` parameters (`pool_size`, `rrf_k`) are still supported.

### Entry Limits

- Default max: **5 000 entries per project** — enforced in `MemoryStore`.
- Profile-configurable: `limits.max_entries` and optional `limits.max_entries_per_group`.

---

## What Changed (v3 delta)

### Storage Engine — SQLite → PostgreSQL

| Aspect | v2 | v3 |
|--------|----|----|
| Private memory | SQLite per agent: `.tapps-brain/agents/<id>/memory.db` | PostgreSQL table `private_memories` keyed by `(project_id, agent_id)` |
| Hive (shared) | SQLite `HiveStore` or Postgres | **Postgres only** — `SqliteHiveBackend` removed |
| Federation | SQLite `FederatedStore` or Postgres | **Postgres only** — `SqliteFederationBackend` removed |
| FTS | SQLite FTS5 | PostgreSQL `tsvector` (Hive/Federation); FTS5 removed from shared stores |
| Agent isolation | File-system path per agent | `(project_id, agent_id)` row scoping in Postgres |
| Startup without DSN | Silent fallback to SQLite | **Hard error** — no DSN → clear exception, non-zero exit |

**Decision rationale:** One engine → one backup model, one security surface, and 2026-realistic
ops for agent fleets. See [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md).

### Audit Emission Timing

| Aspect | v2 | v3 |
|--------|----|----|
| Audit backend | JSONL file at `{store_dir}/memory/memory_log.jsonl` | Postgres `audit_log` table (migration 005) |
| Write path | Synchronous `append()` to JSONL on every mutation | Synchronous `INSERT` into `audit_log` (same thread, same transaction window) |
| v3.0 transitional | v3.0 used `/dev/null` sentinel — audit was a no-op | v3.1+ uses real Postgres `INSERT` via `PostgresPrivateBackend.append_audit` |
| Read path | Line-by-line JSONL scan via `AuditReader` | Indexed query against `audit_log` (indexed on `(project_id, agent_id, timestamp DESC)`) |
| Cross-agent visibility | Single file → inherently single-agent | Row-scoped by `(project_id, agent_id)` — agents cannot read each other's audit rows |

Code references: `src/tapps_brain/migrations/private/005_audit_log.sql`,
`src/tapps_brain/postgres_private.py` (`append_audit`).

### valid_at / invalid_at Semantics

| Aspect | v2 | v3 |
|--------|----|----|
| Column type | `TEXT` (ISO-8601 string, no enforcement) | `TIMESTAMPTZ` — Postgres stores as UTC, comparisons are timezone-aware |
| Timezone handling | Application responsibility; naive strings accepted silently | Naive strings without UTC suffix may be rejected or mis-parsed by psycopg; callers must pass UTC-aware ISO-8601 strings |
| Bi-temporal window | `[valid_at, invalid_at)` — modelled by `MemoryEntry.is_active_at()` | Unchanged semantics; Postgres enforces the column constraint at the storage layer |
| Default | `None` / `None` — entry is always active | Unchanged |

`MemoryEntry.is_active_at()` string-compares ISO-8601 representations — this is unchanged.
The change is that v3 Postgres enforces `TIMESTAMPTZ`; callers passing naive local-time strings
will see different behaviour compared to v2 SQLite TEXT storage.

Code references: `src/tapps_brain/models.py` (`valid_at`, `invalid_at`),
`src/tapps_brain/migrations/private/001_initial.sql`.

### GC Archive Flow

| Aspect | v2 | v3 |
|--------|----|----|
| Archive location | `{store_dir}/memory/archive.jsonl` (JSONL on disk) | Postgres `gc_archive` table (migration 006) |
| Durability | File system — lost if the container FS is ephemeral | Postgres — durable and backed up with the rest of the database |
| Query | `grep` / line scan | Indexed SQL: `(project_id, agent_id, archived_at DESC)` and `(project_id, agent_id, key)` |
| `health()` / CLI `maintenance gc` | Reads file size via `os.stat` | Queries `COUNT(*)` from `gc_archive` |

Code references: `src/tapps_brain/migrations/private/006_gc_archive.sql`,
`src/tapps_brain/gc.py`, `src/tapps_brain/postgres_private.py` (`write_gc_archive`).

### MemoryTier Dimension Constants

`MemoryTier` values and decay half-lives are **unchanged** between v2 and v3:

| Tier | Half-life |
|------|-----------|
| `architectural` | 180 days |
| `pattern` | 60 days |
| `procedural` | 30 days |
| `context` | 14 days |
| `ephemeral` | 1 day |
| `session` | 7 days |

The composite retrieval score weights (relevance 40 %, confidence 30 %, recency 15 %,
frequency 15 %) are also unchanged.

Code references: `src/tapps_brain/decay.py` (`DecayConfig`), `src/tapps_brain/models.py` (`MemoryTier`).

### FTS Ranking

| Aspect | v2 | v3 |
|--------|----|----|
| Private-memory FTS | SQLite FTS5 (BM25-based, native) | In-memory pure-Python Okapi BM25 (`bm25.py`) applied to the Postgres-loaded entry set |
| Hive / Federation FTS | SQLite FTS5 or Postgres `tsvector` | Postgres `tsvector` + GIN index; ranked by `ts_rank_cd` in SQL, then re-ranked by in-memory BM25 via Reciprocal Rank Fusion |
| A/B/C column weighting | Not applicable (single `value` column) | Postgres `setweight` on `key` (A), `value` (B), `tags` (C) before `tsvector` indexing |
| Ranking note | FTS5 BM25 native to the engine | `ts_rank_cd` is not BM25; it uses cover-density ranking. The pure-Python BM25 in `bm25.py` provides BM25 semantics on top of the Postgres results via RRF. For higher ranking fidelity, the upgrade path to ParadeDB `pg_search` is documented in ADR-007. |
| Stemming / tokenisation | FTS5 default (Porter stemmer, ASCII) | Postgres `to_tsvector('english', ...)` (Snowball stemmer, Unicode-aware) |

Code references: `src/tapps_brain/bm25.py`, `src/tapps_brain/fusion.py`,
`src/tapps_brain/postgres_hive.py` (`search`), `src/tapps_brain/migrations/hive/001_initial.sql`.

### Partially Deferred in v3.0 (updated for v3.1+)

The following v2 features used SQLite-backed helpers that were **not ported** to Postgres in v3.0.
Migration status as of v3.1:

| Feature | v2 | v3.0 status | v3.1+ status |
|---------|----|-------------|--------------|
| `DiagnosticsHistoryStore` | SQLite JSONL | No-op (`db_path=/dev/null`) | Postgres table via migration 004 |
| JSONL audit log | `memory_log.jsonl` | No-op via `/dev/null` sentinel | Postgres `audit_log` table via migration 005 |
| GC archive | `archive.jsonl` | File-based | Postgres `gc_archive` table via migration 006 |
| `FeedbackStore` | SQLite | Not supported by `PostgresPrivateBackend` | Planned in EPIC-061 |

Agents reading diagnostics or feedback history on a v3.0 instance receive empty results.
On v3.1+, diagnostics and audit are persisted to Postgres.

### Removed Public API

The following symbols were **removed** from `tapps_brain` public exports in v3:

- `HiveStore` (was `tapps_brain.hive`)
- `AgentRegistry` (SQLite variant)
- `FederatedStore` (was `tapps_brain.federation`)
- `FederationConfig`
- `SqliteHiveBackend`, `SqliteFederationBackend`

Replace with:

```python
from tapps_brain.backends import create_hive_backend, create_federation_backend

hive = create_hive_backend("postgres://user:pass@host/db")
```

---

## References

- [EPIC-059](../planning/epics/EPIC-059.md) — full acceptance criteria
- [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md) — Postgres-only decision
- [ADR-004](../planning/adr/ADR-004-scale-out.md) — original scale-out rationale (narrowed by ADR-007)
- `docs/guides/hive-deployment.md` — Postgres setup for Hive
- `docs/guides/memory-decay-and-fsrs.md` — decay details
- `scripts/load_smoke.py` — concurrent-agent benchmark (see below)

---

## Load Smoke: Concurrent Agents

Two interfaces are provided:

### Pytest benchmark (STORY-066.9 canonical)

**File:** `tests/benchmarks/load_smoke_postgres.py`  
**Marks:** `requires_postgres`, `benchmark` — skipped in unit suite; requires `TAPPS_BRAIN_DATABASE_URL`  
**Pre-SLO:** Results are **informational only** — no hard latency budget is enforced in v3.0.

```bash
# Full 60-second run (50 concurrent agents, default):
TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5433/tapps_brain \
    pytest tests/benchmarks/load_smoke_postgres.py -v -s

# Quick 10-second run (for local validation):
TAPPS_SMOKE_DURATION=10 \
TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5433/tapps_brain \
    pytest tests/benchmarks/load_smoke_postgres.py -v -s

# Via Makefile (see AGENTS.md):
make benchmark-postgres
```

Override env vars:
- `TAPPS_SMOKE_AGENTS` — number of concurrent agents (default 50)
- `TAPPS_SMOKE_DURATION` — wall-clock seconds per agent (default 60)

### Script runner (exploratory / ad-hoc)

**Script:** `scripts/load_smoke.py`  
**Purpose:** Flexible N-agent × M-ops run (not time-bounded).

```bash
export TAPPS_TEST_POSTGRES_DSN="postgres://tapps:tapps@localhost:5432/tapps_test"
python scripts/load_smoke.py --agents 10 --ops 50
```

See `scripts/load_smoke.py --help` for full options.

### Latency budget

**Status: informational only (pre-SLO).** No hard latency ceiling is enforced in v3.0.
The benchmark is intended to provide a baseline for operators choosing hardware and
pool sizes. When a GA SLO is defined it will appear here.

Observed reference results (developer workstation, 50 agents × 60 s, Docker Postgres):

| Metric | Typical range |
|--------|---------------|
| save p95 | 5 – 30 ms |
| recall p95 | 2 – 15 ms |
| hive_search p95 | 10 – 60 ms |

*(Results vary significantly by hardware, network, and pool saturation.)*
