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

### Partially Deferred in v3.0

The following v2 features use SQLite-backed helpers that are **not yet ported** to Postgres in v3.0.
They degrade gracefully (no crash) but do not persist to Postgres:

| Feature | v2 | v3.0 status |
|---------|----|-------------|
| `DiagnosticsHistoryStore` | SQLite JSONL | No-op (`append_audit` is a no-op via `db_path=/dev/null`) |
| `FeedbackStore` | SQLite | Not supported by `PostgresPrivateBackend`; planned in EPIC-059.7 / EPIC-061 |
| JSONL audit log | `memory_log.jsonl` | No-op via `/dev/null` sentinel; planned replacement in EPIC-061 |

Agents that **read** diagnostics or feedback history will receive empty results rather than an error.

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

**Script:** `scripts/load_smoke.py`  
**Purpose:** Validate that N concurrent agents can write and recall memories without interference.  
**Pre-SLO:** Results are **informational only** — no hard latency budget is enforced in v3.0.

Quick run (requires a Postgres DSN):

```bash
export TAPPS_TEST_POSTGRES_DSN="postgres://tapps:tapps@localhost:5432/tapps_test"
python scripts/load_smoke.py --agents 10 --ops 50
```

See `scripts/load_smoke.py --help` for full options.
