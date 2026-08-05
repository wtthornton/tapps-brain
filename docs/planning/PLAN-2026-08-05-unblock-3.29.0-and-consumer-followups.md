# Plan — unblock 3.29.0 (TAP-5633) and close out the nlt-ideas-scout handoff

**Status:** proposed, 2026-08-05
**Author:** session continuing from `.tapps-mcp/session-handoff.md` (branch `release/3.29.0`, HEAD `75d6299`)
**Inputs:** the session handoff, and `NewCompanyIdeas/docs/cross-project/PROMPT-brain-consolidated-handoff.md` (R1–R5)

Two tracks. **Track A blocks everything** — `main` is red, so no PR can merge.
**Track B** is the consumer-facing work; R1 turns out to be answerable today
without shipping anything, and R2 is already built and waiting on Track A.

---

## Track A — TAP-5633: `count()` can exceed `max_entries`

### A.0 What changed since the handoff: the bug is deterministic, not 1-in-23

TAP-5633's body records "roughly 1 run in 23", and the handoff records "passes in
isolation". Both are artefacts of the backend the test ran against, not of load.

Measured this session:

| Backend | Runs | Failures |
|---|---|---|
| No DSN → `InMemoryPrivateBackend` (conftest autouse fixture) | 12 | 0 |
| `TAPPS_BRAIN_DATABASE_URL` → live Postgres | 6 | 2 (`102`, `101`) |

`tests/conftest.py:403-441` injects `InMemoryPrivateBackend` whenever `MemoryStore`
is built with no explicit backend **and no DSN is set**. A local `pytest` run
therefore never exercises the race at all — the persist that happens *outside* the
store lock returns in microseconds. CI sets
`TAPPS_BRAIN_DATABASE_URL` and `TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1`
(`.github/workflows/ci.yml:66-70`), so CI is the only place the window is wide
enough to hit.

**Reproduction command — use this, not a bare pytest run:**

```bash
TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1 \
TAPPS_BRAIN_DATABASE_URL="postgresql://tapps:tapps@localhost:55432/tapps_brain_dev" \
uv run pytest "tests/unit/test_concurrent.py::TestConcurrentSaveAtCapacity::test_concurrent_save_at_max_capacity" -q -p no:randomly
```

(`tapps-brain-dev-db` is already running on host port 55432. Without
`TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1` the run dies in
`postgres_connection.py:381` on the privileged-role guard, which looks like a
failure but is not this bug.)

This changes the acceptance work: "50 consecutive green runs" is only meaningful
against Postgres. Against the in-memory backend it proves nothing.

### A.1 Mechanism — **corrected 2026-08-05 by measurement**

> TAP-5633's body, and the first draft of this section, both said the overshoot
> enters through `count()`'s unbounded merge. **That is wrong.** Instrumenting
> `_merge_durable_entries` showed the cache is *already* over the cap before
> `count()` is ever called:
>
> ```
> trial=3: {'cache': 101, 'merges_over_cap': 22, 'samples': [(100, 101), (101, 101), ...]}
> trial=5: {'cache': 102, 'merges_over_cap': 42, 'samples': [(100, 101), (101, 101), ...]}
> ```
>
> The first over-cap sample is always `(100 → 101)`: a merge that takes the cache
> from exactly the cap to cap+1. The real entry point is **`_handle_conflicts`,
> which runs the same merge on the *save* path** (`store.py:2035`), before
> `_enforce_entry_caps_before_assign`. Saving with `conflict_check=False` does not
> reproduce it.
>
> `count()` is a *reporter* of the overshoot, not its cause. The fix location is
> unchanged — bound the merge — but the invariant it protects is the save path.

The corrected sequence:

1. `save()` assigns under the lock, then persists **outside** it
   (`_persist_or_rollback`).
2. A concurrent thread evicts that key — cache pop + durable delete — while the
   persist is still in flight. The durable delete is a no-op (the row is not
   there yet); the persist then lands as an **orphan**: durably present, no cache
   slot. `_drop_if_concurrently_removed` reaps it, but only after the persist
   returns.
3. Meanwhile another save calls `_handle_conflicts` → `_merge_durable_entries`,
   which pulls that orphan in. Cache: cap → **cap + 1**.
4. `_enforce_entry_caps_before_assign` then evicts one and assigns one. Net
   change zero — **the overshoot never drains.** It is pinned at cap+1 (or cap+2
   after a second such merge) for the life of the store.

That last step is why this presents as a stable 101/102 rather than a flicker.

### A.1b Original (incorrect) mechanism, kept for the record

The cache side of the cap is correct. `_enforce_entry_caps_before_assign`
(`store.py:4887-4911`) runs under the store lock and evicts 1-for-1 before each
new assign, so `len(self._entries)` never exceeds `max_entries`.

The overshoot enters through the durable side:

1. `save()` assigns under the lock, then persists **outside** it
   (`_persist_or_rollback`, `store.py:2488-2498`).
2. A concurrent thread can evict that key from the cache — cache pop + durable
   delete — while that persist is still in flight. The durable delete is a no-op
   (the row is not there yet); the persist then lands and the row exists durably
   with no cache slot.
3. `_drop_if_concurrently_removed` (`store.py:2500-2517`) cleans exactly this up —
   but only *after* the persist returns. Between commit and cleanup there is a
   real window, ~1 round-trip wide against Postgres and ~0 wide in-memory.
4. `_merge_durable_entries` (`store.py:882-906`) calls `load_all()` with **no
   limit** and merges every durable row not already cached. Land it inside that
   window and the cache goes to 101/102, and `count()` reports it.

The removal-epoch tombstone guard does not help here: the orphan row was never
"removed after the snapshot", it was *added* after it.

Five threads → observed 101 and 102, i.e. one or two orphans in flight. Consistent.

### A.2 Fix

Bound the merge inside `_merge_durable_entries` so every caller inherits it. Do
**not** patch `count()` — `list_all()`, `snapshot()`, `list_memory_groups()`,
`_count_entries_in_memory_group` and the recall path all reach the same code.

```python
def _merge_durable_entries(self, *, limit: int | None = None) -> None:
    cap = self._max_entries
    with self._serialized():
        snapshot_epoch = self._removal_epoch
        room = cap - len(self._entries)
    if room <= 0:
        return                      # cache is already at the invariant
    effective = min(limit, cap) if limit is not None else cap
    durable = self._persistence.load_all(limit=effective)
    with self._serialized():
        room = cap - len(self._entries)   # re-read: concurrent saves may have filled it
        for entry in durable:
            if room <= 0:
                break
            ...existing skip/tombstone checks...
            self._entries[entry.key] = entry
            self._index_entry_entities(entry.key, entry.value)
            room -= 1
```

`load_all` already orders `updated_at DESC` and honours `limit` as an early cutoff
(`postgres_private.py:305-334`), so the bounded merge keeps the *newest* durable
rows — the same ones an unbounded merge followed by confidence eviction would tend
to keep.

Two follow-through requirements the handoff calls out, both honoured:

- **Do not hide lagging eviction.** Silently truncating would report
  `count() <= max_entries` while durable genuinely holds more. So: when
  `load_all` returns `effective` rows *and* room ran out, emit a
  `store.merge.durable_overshoot` metric and a debug log with the observed
  durable size. The overshoot stays visible; it just stops corrupting `count()`.
- **Convergence.** AC 3 asks that concurrent overflow settles to
  `count() <= max_entries`. It does, because the orphan is transient —
  `_drop_if_concurrently_removed` deletes it a round-trip later. The regression
  test must assert convergence explicitly (poll `count()` after the threads join
  until stable, with a bounded wait), not just the instantaneous value.

### A.3 Call-site audit — the risk in this change

`_merge_durable_entries` has ~18 call sites (`store.py`, `_store_query.py`,
`_store_integrity.py`, `_store_relations.py`, `auto_consolidation.py`). Bounding it
by `max_entries` is safe *only* if no caller depends on seeing more durable rows
than the cap. Before landing, check each one, in particular:

- `gc.py` / archival paths — do they need to see rows beyond the cap to archive
  them? If yes they must query durable directly, not via the cache merge.
- `auto_consolidation.py:355` — already documents a concurrency interaction with
  this function; re-read that comment against the new bound.
- `_store_integrity.py:64,260` — integrity checks that enumerate everything are
  the most likely to regress.

Any caller that genuinely needs the full durable set should be moved off the cache
merge rather than having the bound weakened. Record the audit result in the PR
body; this is where a wrong call ships a silent data-visibility regression.

### A.4 Secondary payoff — pool pressure

The handoff records `psycopg_pool.TooManyRequests` with 20 waiters, reached via
`recall` → `_visible_entry_count` → `list_all` → unbounded `load_all`. Bounding
the merge caps that scan at `max_entries` rows per call. Worth confirming with a
before/after on `pool.waiting` during the concurrency test, and worth a line in
the PR body — but it is a side effect, not the fix's justification.

### A.5 Steps

1. Branch from `main` (not from `release/3.29.0`): `tap-5633-bound-durable-merge`.
   The fix must land on `main` first — `main` is red, so this is a repo-wide
   unblock, not a 3.29.0 change.
2. Land the regression test **first, red**: distinct-key concurrent overflow (the
   TAP-5615 shape), asserting both the cap and convergence. Skip-marked when no
   DSN is set, so it is honest about needing Postgres.
3. Apply the bound in `_merge_durable_entries`. Add the overshoot metric.
4. Complete the A.3 call-site audit; adjust any caller that needs the full set.
5. Verify: 50 consecutive runs of the capacity test **with the DSN set**. Then the
   TAP-5614 write-loss tests, then the full unit suite with the DSN.
6. `uv run ruff check`, `ruff format --check`, `mypy --strict src/tapps_brain/`
   locally before push — per-file validation scores do not catch cross-file lint
   or strict-mypy breakage.
7. Merge to `main`. Confirm `main` CI green via `workflow_dispatch`.
8. Rebase `release/3.29.0` on `main`, confirm PR #241 green, merge, tag `v3.29.0`.
9. `make hive-deploy` (both `tapps-brain-http` and `tapps-visual` to 3.29.0), then
   `brain-smoke-live`.

### A.6 Also worth fixing while here (separate commit, same PR or a follow-up)

The in-memory-backend fixture means **the entire unit suite runs against a
different backend locally than in CI**. That is why this bug shipped to `main`
undetected and why the handoff's "passes in isolation" reading was formed. Options,
cheapest first:

- Document the DSN-set reproduction in `CONTRIBUTING`/`AGENTS.md` and in
  `scripts/release-ready.sh` output. (Do this now.)
- Add a `pytest -m postgres` marked subset that CI already covers and that a
  developer can run locally against `tapps-brain-dev-db`. (File as its own issue.)

Do not "fix" it by removing the fixture — the fixture is what keeps the suite
runnable with no Postgres.

---

## Track B — the nlt-ideas-scout consolidated handoff (R1–R5)

### B.1 R1 — **Answered: `conflict_check` is per-project.** No code change needed.

This was the one item blocking the consumer, and the answer is verifiable today.

Their inference — "the container has no profile env vars, so it serves one profile
per server" — conflated two different things that share the word *profile*:

- `TAPPS_BRAIN_DEFAULT_PROFILE` selects an **MCP tool-visibility profile**:
  `ProfileRegistry` maps a profile name → a frozenset of *tool names*
  (`mcp_server/profile_registry.py:1-38`). It has nothing to do with
  `conflict_check`.
- `conflict_check` lives on `MemoryProfile`, which the store resolves **per
  project** from the `project_profiles` registry table (ADR-010) whenever
  `TAPPS_BRAIN_PROJECT` and `TAPPS_BRAIN_DATABASE_URL` are set
  (`store.py:1009-1051`). The HTTP/MCP path sets `TAPPS_BRAIN_PROJECT` per request
  from the caller's project id before constructing the store
  (`mcp_server/context.py:296-315`), and caches one store per
  `(project_id, agent_id)`.

Confirmed against the running deployment, not inferred:

```
$ docker exec tapps-brain-db psql -U tapps -d tapps_brain -tAc \
    "select project_id, approved, source, profile->'conflict_check'
     from project_profiles where project_id like '%scout%';"
nlt-ideas-scout|f|auto|{"aggressiveness": "medium", "similarity_threshold": null}
```

90 projects are registered, 14 approved. `nlt-ideas-scout` already has its own
profile row with its own `conflict_check` block. `approved=false` does not matter
for resolution — `project_registry.py:12` states resolution uses a registered row
at *any* `approved` value.

**So `per_tier.context: 0.95` can be applied to `nlt-ideas-scout` alone, and no
other consumer's semantics change.** That is the answer they asked for, and it
unblocks them against deployed 3.28.3 with no release.

**Two operational gotchas that must go in the reply:**

1. `_StoreCache` (`mcp_server/context.py:150-194`) is an LRU with **no TTL**. The
   profile is resolved once at `MemoryStore` construction. A registry write does
   **not** take effect for an already-cached store — the container must be
   restarted (or the entry LRU-evicted) before the new threshold is live. Any
   verification run before that restart will look like the change did nothing.
2. The row is `source=auto, approved=false`. Re-register it deliberately via
   `POST /admin/projects` with the full profile JSON and `approved=true`, rather
   than hand-editing the JSONB, so the record is admin-owned and the change is
   auditable.

**Steps:**

1. `GET /admin/projects/nlt-ideas-scout` → current profile JSON.
2. Merge `conflict_check.per_tier = {"context": 0.95}` into it.
3. `POST /admin/projects` with the merged profile, `approved=true`,
   `source=admin`, `notes` recording why and pointing at their prompt.
4. Restart `tapps-brain-http` (gotcha 1).
5. Re-read `GET /admin/projects/nlt-ideas-scout` to confirm persisted.
6. Tell them to run their three-save probe; pass condition is theirs
   (all three recallable, `invalidated` empty on all three, same-key replacement
   still works).

Write it up as a short runbook in `docs/guides/` — this is the first per-project
profile override applied in production and the next one should not require
re-deriving any of the above.

### B.2 R2 — `supersede: "key-scoped"` is already built; it ships with 3.29.0

Commit `c8e11d5` landed the passthrough across memory_save / save_many / async /
HTTP / MCP / AgentBrain, with `tests/unit/test_supersede_scope.py`. Nothing to
design. It is gated entirely on Track A.

Before telling the consumer it is live, verify against the **deployed** container,
not the source — the same discipline they used on 3.28.3:

- `POST /v1/remember` with `supersede: "key-scoped"` leaves other keys' validity
  intervals open;
- omitted / `"global"` is byte-identical to 3.28.3 behaviour;
- an invalid value returns a typed 400, not a silent fallback;
- the flag is honoured on `/v1/remember:batch` and MCP `brain_remember`.

### B.3 R3 — `invalidated_detail` with the similarity score

They rate this above the threshold change, and it is the cheapest item on the
board: **the data is already computed and already discarded.**

`plan_conflicts` builds `audit = [{key, similarity, tier}, ...]`
(`_save_conflict.py:78-86`) from `SaveConflictHit.similarity`
(`contradictions.py:47-51`). Today only `report["invalidated"]` — a bare key list
(`store.py:1343`) — reaches `_save_envelope` (`memory_service.py:1087-1090`).

**Change:** populate `report["invalidated_detail"]` from `plan.audit`, and merge it
into the envelope beside `invalidated`. Keep `invalidated` unchanged — the
consumer gates on it today and a shape change there is a breaking contract change.

Surfaces: `store.py` (report), `services/memory_service.py` (`_save_envelope`,
shared by `memory_save` and `memory_save_many`), `http_adapter.py:1299` docstring,
MCP `brain_remember` docs, OpenAPI snapshot regenerated. Include the effective
threshold that was applied per save — `ConflictPlan.similarity_threshold` is
already on the plan, and "evicted at 0.71 against a threshold of 0.6" is the
sentence that lets them tune. Target 3.29.1.

### B.4 R4 — make `per_tier` discoverable

Their diagnosis is right and the two placements they suggest are the right two:

1. Wherever `invalidated` / `invalidated_detail` is documented (HTTP field docs,
   MCP tool description, `docs/guides/agentforge-integration.md`) — a consumer
   seeing invalidations is exactly the consumer who needs the knob.
2. `profile onboard` output (`onboarding.py`, `services/profile_service.py`) —
   it already renders agent-facing guidance.

Add a third that R1 exposed: document **that profiles are per-project and how to
set one** (the B.1 runbook). The knob being undiscoverable was only half their
problem; the other half was not knowing a per-project override was possible at
all, which is what made them unwilling to ask for the tuning. Ship with B.3.

### B.5 R5 — is 0.6 the right default for `context`?

Their argument is sound: `ConflictCheckConfig`'s own docstring justifies `per_tier`
by describing ingest-heavy `context` workloads at ~0.6 — i.e. the documented
motivating case for the override is the default being wrong for the tier's purpose.

But do not change a default on one consumer's report. We have the cross-consumer
data they lack, so **measure before deciding**:

1. Query the audit log for save-conflict invalidations grouped by `(project_id,
   tier)` with the similarity distribution — the per-hit score is already written
   into the audit payload.
2. Decide from the distribution: if `context` invalidations cluster just above
   0.6 across *multiple* projects, that is the mis-calibration they describe. If
   they are one project's key-space, `per_tier` is the correct answer and the
   default stays.
3. If the default does move, it is a semantic change for every consumer: minor
   version, CHANGELOG entry naming the old and new value, and a heads-up to the
   registered projects that currently rely on `context` supersede.

Track as its own issue with the measurement as the first acceptance criterion.
Not 3.29.x.

### B.6 Delivery — two replies are written but unsent

`docs/cross-project/PROMPT-brain-remember-write-loss.RESPONSE.md` and
`...similarity-supersede-across-keys.RESPONSE.md` exist in this repo and have never
reached the consumer. Both predate the R1 finding, so the supersede one now needs a
correction before it goes: it recommends `per_tier: context: 0.95` without
establishing the per-project scoping that was their blocker, and without the
store-cache restart gotcha.

`agent-scope.md` forbids writing into the `NewCompanyIdeas` repo. Delivery is:
finalise both replies here, then hand the paths to the user to copy across.

---

## Sequencing

| # | Item | Blocked by | Ship vehicle |
|---|---|---|---|
| 1 | A — TAP-5633 bounded merge → `main` green | — | `main` |
| 2 | B.1 — R1 answer + per-project override for `nlt-ideas-scout` | nothing (do in parallel with 1) | ops + runbook |
| 3 | B.6 — send both replies, corrected with the R1 finding | 2 | docs |
| 4 | A — rebase, merge PR #241, tag v3.29.0, deploy | 1 | 3.29.0 |
| 5 | B.2 — verify `supersede: "key-scoped"` against the deployed container | 4 | verification |
| 6 | B.3 + B.4 — `invalidated_detail` + discoverability docs | 4 | 3.29.1 |
| 7 | B.5 — measure the `context` default | 6 (needs the score data) | own issue |

Items 2 and 3 do not depend on Track A and unblock the consumer today. That is the
main reason not to run this strictly in order.

## Housekeeping carried from the handoff

- 3 stale branches verified safe to delete, held while a release is mid-flight —
  delete after step 4, not before.
- 67 uncommitted files belong to a **concurrent session**. Stage explicitly by
  path; never `git add -A`.
- TAP-5459 tree closure is already sequenced in `prompts/tap-5459-close-phase-1.md`
  and is independent of everything above.
- TAP-5636 (dev-deploy smoke races container startup) will bite during step 4's
  deploy — expect it, do not re-diagnose it.

## Execution checklist

Ordered. Items marked ‖ run in parallel with the critical path and unblock the
consumer without waiting for the release.

**Critical path — get `main` green**

1. [ ] Branch `tap-5633-bound-durable-merge` **from `main`**. Not from
       `release/3.29.0`: the fix has to land where the red is.
2. [ ] Write the regression test first and watch it fail: distinct-key concurrent
       overflow (TAP-5615 shape), asserting the cap *and* convergence (poll
       `count()` after join until stable, bounded wait). Skip-mark it when
       `TAPPS_BRAIN_DATABASE_URL` is unset so it never green-washes a no-DSN run.
3. [ ] Bound `_merge_durable_entries` by remaining room under `max_entries`
       (`store.py:882`). Re-read room under the lock after `load_all` returns.
4. [ ] Add the `store.merge.durable_overshoot` metric + debug log so lagging
       eviction stays visible instead of being truncated away.
5. [ ] Audit the ~18 call sites. `gc.py`, `_store_integrity.py:64,260`,
       `auto_consolidation.py:355` are the three that could legitimately need more
       than `max_entries` rows. Record the result in the PR body.
6. [ ] Verify, all with the DSN set:
       50× the capacity test → TAP-5614 write-loss tests → full unit suite.
7. [ ] `uv run ruff check src/ tests/`, `ruff format --check`,
       `mypy --strict src/tapps_brain/`. Per-file scores do not catch cross-file
       lint or strict-mypy breakage.
8. [ ] Merge to `main`; confirm green with a fresh `workflow_dispatch` run.
9. [ ] Document the reproduction recipe (DSN **and**
       `TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1`) in `AGENTS.md` and in
       `scripts/release-ready.sh` output.

**Then — ship 3.29.0**

10. [ ] Rebase `release/3.29.0` on `main`; confirm PR #241 green; merge; tag
        `v3.29.0`.
11. [ ] `make hive-deploy` (http + visual), then `brain-smoke-live`. Expect
        TAP-5636 (smoke races container startup) — do not re-diagnose it.
12. [ ] Verify `supersede: "key-scoped"` against the **deployed container**:
        key-scoped leaves other keys' intervals open; omitted/`"global"` is
        byte-identical to 3.28.3; an invalid value returns a typed 400; honoured on
        `/v1/remember:batch` and MCP `brain_remember`.
13. [ ] Delete the 3 stale branches — only now, not before the tag.

**‖ Parallel — unblock the consumer today**

14. [ ] **(needs go-ahead — production write to another project's registry row)**
        `GET /admin/projects/nlt-ideas-scout` → merge
        `conflict_check.per_tier = {"context": 0.95}` → `POST /admin/projects`
        with `approved=true, source=admin` → **restart `tapps-brain-http`** →
        re-read to confirm.
15. [ ] Correct both `docs/cross-project/*.RESPONSE.md` with the R1 finding
        (per-project scoping) and the store-cache restart gotcha; hand the paths
        to the user to copy into `NewCompanyIdeas` (agent-scope forbids writing
        there).
16. [ ] Write the per-project profile override runbook in `docs/guides/` — first
        one applied in production; the next should not re-derive it.

**After the release — 3.29.1 and beyond**

17. [ ] `invalidated_detail` (R3): populate from `plan.audit`, merge into the save
        envelope beside an unchanged `invalidated`, include the effective
        threshold. Regenerate the OpenAPI snapshot.
18. [ ] Discoverability (R4): document `per_tier` at the `invalidated` field docs,
        in `profile onboard` output, and document that profiles are per-project.
19. [ ] File the R5 measurement issue: similarity distribution of `context`
        invalidations grouped by `(project_id, tier)` from the audit log, as the
        first acceptance criterion. Do not move the default before that.

## Open decision for the user

Nothing in Track A. In Track B, one call is yours: **B.1 step 3 writes to the
production project registry for another project's tenant** (`nlt-ideas-scout`).
It is a read-modify-write of that project's own profile row and reversible, but it
changes a live consumer's memory semantics, so it wants an explicit go-ahead
rather than being folded into "fix TAP-5633".
