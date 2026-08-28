# ADR-014: Scheduled decay refresh alongside lazy decay (TAP-6697)

**Status:** Accepted
**Date:** 2026-08-27
**Owner:** @wtthornton
**Supersedes:** the deferral clause in [ADR-002](ADR-002-freshness-lazy-decay-vs-ttl.md) only

## Context

[ADR-002](ADR-002-freshness-lazy-decay-vs-ttl.md) (2026-04-03) chose **lazy decay on read** as
the freshness model and explicitly deferred a batch pass, naming it:

> **`maintenance decay-refresh`** (or equivalent) batch "touch all rows" refresh —
> **deferred** until a concrete ops or product requirement needs wall-clock alignment
> (would need design for SQLite write volume and lock interaction).

Two things changed since.

1. **The stated objection is void.** [ADR-007](ADR-007-postgres-only-no-sqlite.md) removed
   SQLite entirely (stage 2, 2026-04-11). There is no in-process database, no writer lock to
   contend for, and no write-amplification concern of the shape ADR-002 was guarding against.
   A batched `UPDATE` over a Postgres table with MVCC is an ordinary maintenance write.

2. **Lazy decay alone leaves a real gap.** Read-path decay recomputes an entry's effective
   confidence on every recall but never writes the conclusion down. A row that decayed past
   the stale threshold years ago is still `status='active'` with `invalid_at IS NULL`, so it:

   - occupies a top-K slot in the FTS and KNN channels before ranking ever sees it, and
   - is returned outright by a query with no better candidate, because the read-path floor
     is a *ranking* input, not an *exclusion*.

   The corpus therefore only grows. This is the same failure shape TAP-5547 fixed on the
   trust axis (a candidate nobody validated stays a candidate forever) and TAP-6697 fixed on
   the validity axis (`close_validity`), applied to the freshness axis.

The prerequisite that was missing in April now exists: TAP-6697 added a single helper,
`MemoryStore.close_validity(key, reason, superseded_by=None)`, that writes `invalid_at`,
lifecycle `status` and one audit row together. A refresh pass has somewhere to route its
decision that is consistent with contradiction, supersession and consolidation.

## Decision

1. **Lazy read-path decay is unchanged.** `decay.calculate_decayed_confidence` remains the
   authority for ranking, and nothing in this ADR introduces a background thread, timer or
   scheduler inside the library. ADR-002's core stance survives.

2. **Add a scheduled refresh as an operator-invoked pass**, exposed as
   `tapps-brain maintenance decay-refresh --dry-run | --apply --report <path>` over
   `MemoryStore.refresh_decay()`. Per live row (`status='active'`, no closing bound, no
   `superseded_by`):

   - effective confidence at or below the tier's confidence floor (`_get_confidence_floor`),
     for `floor_retention_days` → archive to `gc_archive` with `archive_reason='age'`, then
     delete from the live table;
   - otherwise below `stale_threshold` → `close_validity(key, reason='age')`, which writes
     `invalid_at`, `status='stale'`, `stale_reason`, `stale_date` and one audit row.

   The decision half (`decay.identify_decay_refresh`) is pure, so the dry-run report and the
   apply pass consume the identical list — a dry run cannot describe a different row set
   than the apply would touch.

3. **The floor branch is evaluated before the stale branch.** `MemoryGarbageCollector`
   deliberately never auto-archives `status='stale'` rows (they are flagged for human
   review). Marking a floor-crossing row stale on one pass and archiving it on a later one
   would therefore strand it forever. A row deep below the floor is archived outright.

4. **`archive_reason='age'` is a distinct reason code from GC's `floor_retention`.** They
   are different passes with different triggers: GC's sweep is on-demand and layers a
   retention grace period plus the contradicted and session-expiry rules on top; this pass
   is the scheduled decay curve alone. Sharing one code would make the two
   indistinguishable in `gc_archive`, which is the exact audit question the column answers.
   The reason travels in the existing `payload` JSONB (`payload->>'archive_reason'`) — no
   new column, additive for every existing reader.

5. **`--apply` is never the default and never runs blind.** `--dry-run` is the default;
   `--apply` must be asked for. The dry-run report names the `gc_archive` rows that back
   an undo, so an operator relaying an ACCEPT already knows what the pass is recoverable
   from.

## Consequences

- **`decay.py`** gains `identify_decay_refresh` / `DecayRefreshAction` next to the existing
  `identify_learning_demotions` / `LearningDemotion`, and delegates the floor-age inversion
  to `MemoryGarbageCollector._days_at_floor` so the refresh and GC cannot drift apart on the
  decay curve.
- **`store.py`** gains `refresh_decay()` and `_archive_and_delete()`; the latter is the
  archive-before-delete gate `gc()` already used (delete only what `archive_entry` confirmed,
  skip rows a concurrent save touched).
- **Idempotence:** a closed row is no longer `status='active'`, so re-running the pass is a
  no-op on it. Nightly scheduling is safe.
- **Known follow-up (not addressed here):** a row closed as `stale` that *later* falls below
  the floor is not archived by this pass, because it is no longer `active` — and GC will not
  auto-archive a stale row either. That is GC's pre-existing "stale rows await human review"
  policy, not a regression introduced here; changing it needs its own decision about whether
  age alone may archive a row a human was asked to look at.
- **Not a dreaming pass.** Every rule here is deterministic arithmetic over stored fields.
  No LLM is involved, in line with the project's no-LLM-at-runtime stance.

## Non-goals

- No background TTL worker, cron thread or scheduler inside the library — ADR-002's ruling on
  that stands. Scheduling is the deployment's job (a maintenance job invoking the CLI).
- No compliance-grade "time-bounded physical deletion". Rows are archived, never dropped.
- No change to tier half-lives, the temporal-sensitivity multipliers, the stale threshold or
  the confidence floors. This pass consumes those values; it does not retune them.

## Supersedes / updates

- **Supersedes** ADR-002's deferral of `maintenance decay-refresh` and its SQLite
  write-volume / lock-interaction rationale, which [ADR-007](ADR-007-postgres-only-no-sqlite.md)
  made void. ADR-002's decision to keep lazy decay as the default freshness model is
  **retained**, not overturned.
- Depends on [ADR-007](ADR-007-postgres-only-no-sqlite.md) (Postgres-only persistence).
- Builds on TAP-6697's `close_validity` helper, which this pass calls for the age branch.
