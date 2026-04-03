# ADR-002: Freshness — lazy decay + operator GC (defer wall-clock TTL jobs)

**Status:** Accepted  
**Date:** 2026-04-03  
**Owner:** @wtthornton  
**Epic / story:** [EPIC-051](../epics/EPIC-051.md) — STORY-051.2  
**Context:** [features-and-technologies.md](../../engineering/features-and-technologies.md) section 10 checklist item 2

## Context

Checklist item 2 asks whether **lazy decay** and **consolidation threshold tuning** are enough, or whether the product needs **explicit TTL-style jobs** (scheduled passes that age or remove memories on a wall-clock cadence independent of reads).

Today:

- **`decay.py`** applies **exponential decay on read** (tier half-lives, profile overrides, optional FSRS-lite stability updates) — **no background timers** in core.
- **`gc.py`** / **`MemoryStore.gc`** identify archive candidates using decayed confidence, floor retention, session expiry, and contradicted thresholds; operators run **CLI / MCP maintenance** (`maintenance gc`, `maintenance stale`, dry-run summaries).
- **Consolidation** thresholds (`auto_consolidation`, EPIC-044) tune merge aggressiveness separately from decay.

## Decision

1. **Shipped / maintained path (do):** Keep **lazy decay** as the default freshness model for core. Pair it with **operator-invoked GC** and **profile-tunable** half-lives, ceilings, floors, and consolidation settings. Documented product stance for decay vs FSRS fields remains in [`memory-decay-and-fsrs.md`](../../guides/memory-decay-and-fsrs.md) (EPIC-042.8).

2. **Out of scope for core / deferred (not shipping now):**
   - **Mandatory background TTL workers** or cron-shaped jobs inside the library that rewrite confidence or delete rows on a schedule **without** a read path.
   - **`maintenance decay-refresh`** (or equivalent) batch “touch all rows” refresh — **deferred** until a concrete ops or product requirement needs wall-clock alignment (would need design for SQLite write volume and lock interaction).
   - **Metrics:** rolling counter of entries **crossing a stale threshold per day** — **deferred** until observability trigger work (e.g. save-path / lifecycle tuning) explicitly requires it; existing GC dry-run / health fields cover operator review without a new time-series.

Revisit with a **new** story or ADR if fleet operators need **guaranteed** max retention without ever reading an entry, or if compliance requires **time-bounded physical deletion** independent of GC scheduling.

## Consequences

- **`decay.py` / `gc.py`:** Remain the canonical implementation; no new core thread pool or scheduler.
- **Operators** continue to rely on **`maintenance gc`**, **`maintenance stale`**, and profile decay/GC config for lifecycle control.
- **Consolidation** remains a separate knob from TTL; threshold sweep / undo paths (EPIC-044.4) stay the supported way to tune merge behavior.

## References

- [`features-and-technologies.md`](../../engineering/features-and-technologies.md) — section 1 (stale / decay row), section 10 checklist.
- [`memory-decay-and-fsrs.md`](../../guides/memory-decay-and-fsrs.md) — hybrid decay + FSRS-lite behavior.
- [`EPIC-051.md`](../epics/EPIC-051.md) — STORY-051.2.
