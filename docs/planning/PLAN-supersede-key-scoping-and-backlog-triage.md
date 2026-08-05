# Plan — key-scoped supersede, and what is actually urgent

**Status:** proposed, 2026-08-05
**Prompted by:** `PROMPT-brain-similarity-supersede-across-keys.md` (nlt-ideas-scout)
**Current release in flight:** 3.29.0 (staged, awaiting two `docker/` env edits)

---

## 1. What the consumer hit, and what is actually true

`POST /v1/remember` closes the validity interval of entries under **other** keys when
the incoming value is textually similar. The consumer sees paid research dossiers
drop out of recall.

Verified against the code, not inferred:

| Claim | Verdict |
|---|---|
| Cross-key supersede is a bug | **No — it is the design.** `plan_conflicts` passes `exclude_key=key`, deliberately excluding the *same* key and scanning every other entry. |
| It is similarity-driven | **Yes.** `detect_save_conflicts` scores `text_similarity` against a sentinel entry. |
| It is embedding-based | **No — token/Jaccard-style.** No embedding is involved in the conflict path. |
| Tier is irrelevant | **No.** Conflicts only fire *within the same tier*, and the threshold is per-tier configurable. |
| Threshold | `aggressiveness: medium` → **0.6** by default. `low` = 0.75, `high` = 0.45. |

**This exact workload is already documented as the motivating case.**
`ConflictCheckConfig` (`src/tapps_brain/profile.py:283-287`) says `per_tier` exists for
"ingest-heavy `context` workloads where many distinct facts are ~0.6 similar and would
otherwise invalidate each other (TAP-4464)." The consumer is on `tier: context` at the
default 0.6.

So there is an existing escape hatch, and a real gap beside it.

## 2. The gap

`MemoryStore.save` already accepts `conflict_check: bool`, and
`services/memory_service.py:140` already uses `conflict_check=False` internally on the
supersede path. It is simply **not plumbed through** `/v1/remember`, `/v1/remember:batch`,
or MCP `brain_remember`. No external caller can turn it off per save.

The consumer's first-choice ask — "a per-save flag so this key-space only supersedes
its own key" — is therefore a passthrough, not a new feature.

## 3. Proposed change

### 3.1 Expose per-save supersede scope (the fix)

Add an optional request field on the save surfaces:

```
supersede: "global" (default, current behaviour) | "key-scoped"
```

`"key-scoped"` maps to `conflict_check=False` on the store call. Prefer this over
exposing `conflict_check` verbatim: the boolean names an implementation detail, whereas
`supersede` names the semantics the caller reasons about, and leaves room for a future
`"tier-scoped"` without another contract change.

Surfaces to thread it through:

1. `src/tapps_brain/http_adapter.py` — `/v1/remember`, `/v1/remember:batch` request models
2. `src/tapps_brain/services/memory_service.py` — `memory_save`, `memory_save_many`
3. `src/tapps_brain/mcp_server/standard.py` — `brain_remember`
4. `src/tapps_brain/agent_brain.py` — `AgentBrain.remember()`

Default must stay `"global"`. Changing the default would silently alter behaviour for
every other consumer, and supersede is correct for its intended workload (competing
claims about one fact).

### 3.2 Document the profile knob (the today answer)

The consumer does not have to wait for 3.1. This works against deployed 3.28.3:

```yaml
conflict_check:
  per_tier:
    context: 0.95
```

Document it in `docs/guides/agentforge-integration.md` alongside the `invalidated`
field, since that field is what made the behaviour visible.

### 3.3 Report the similarity score

`invalidated` currently lists keys only. The audit payload already carries the
similarity score per hit (`plan.audit`). Surfacing it — `invalidated_detail: [{key,
similarity}]` — lets a consumer tune a threshold from their own traffic instead of
guessing. Cheap; the data is already computed and currently reaches only a log line.

## 4. Acceptance

- [ ] `supersede: "key-scoped"` on `/v1/remember` leaves entries under other keys untouched
- [ ] Default (`"global"`, or field omitted) is byte-identical to 3.29.0 behaviour
- [ ] The flag is honoured on `/v1/remember:batch`, MCP `brain_remember`, and `AgentBrain.remember()`
- [ ] An invalid `supersede` value returns a typed 400, not a silent fallback
- [ ] `invalidated_detail` reports the similarity that triggered each invalidation
- [ ] OpenAPI snapshot regenerated; the new field documented
- [ ] `pytest -m "not benchmark"`, `ruff`, `ruff format`, `mypy --strict` clean

## 5. Urgency — honest assessment

**Not a hotfix.** No data is lost: invalidation closes a validity interval, the rows
remain, and the behaviour is tunable by config today without shipping anything. That is
categorically unlike TAP-5614, where acknowledged writes vanished.

It is small and well-understood, so it belongs in **3.29.0** if that release has not
yet been tagged, and in 3.29.1 otherwise. The consumer should be told about §3.2
immediately either way — it unblocks them against the currently deployed build.

## 6. Backlog triage — measured, not inferred

### 6.0 Headline: the TAP-5459 tree is obsolete, and CI has a real coverage gap

Ran the full integration suite against a live Postgres:

```
685 passed, 13 skipped, 0 failed in 112.44s
```

**There are no DB-dependent integration failures.** Commit `938b8d2` (2026-08-03,
"make the last 4 DB-dependent integration failures pass") already closed them; its
message records the same number — "685 passed, 0 failed against a live Postgres, down
from 12 failures."

Every open child of TAP-5459 describes a failure to diagnose and fix. Those failures do
not exist. The tree is stale, not actionable.

Worse, the issue bodies are not merely stale — they are largely fabricated. Audited all
ten (TAP-5459 through TAP-5468):

- Every body is **exactly 500 characters** — a generation cap, not authored prose.
- **8 of 10 referenced file paths do not exist in the repo.** Only
  `test_tenant_isolation.py` and `test_rls_force_owner_guard.py` are real. Named-but-absent
  files include `test_pg_adapter_profiles.py`, `test_rls_experience_events.py`,
  `test_postgres_observability.py`, `test_kg_store_retrieval.py`,
  `test_session_chunking.py`, `test_experience_events_flywheel.py`.
- Named-but-absent tests include `::test_cache_coherence`, `::test_retrieve_profiles`,
  `::test_populate_retrieve`.
- TAP-5460 cites `src/tapps_brain/migrations/postgres/001_db_roles.sql`; the real file is
  `src/tapps_brain/migrations/roles/001_db_roles.sql` (155 lines), whose header already
  documents the ordering requirement it claims is a trap.

These read as machine-filed stubs that invented plausible test paths. Meanwhile the real
3.28.2 fixes cited under TAP-5461/5462/5463 landed in *different* files than the issues
name (`test_profile_filter.py`, `test_experience_events_migration.py`,
`docs_lookup.py`). The numbers were reused for unrelated real work.

### 6.1 The real remaining work

CI runs **18 of 56** integration files. The other 38 are excluded by a comment that is
now false:

```
# The DB-dependent integration files ... remain excluded — they have known
# pre-existing failures tracked in TAP-2803.       ← .github/workflows/ci.yml:110
```

The CI `test` job **already provisions a Postgres service container and applies
migrations**. It has everything needed to run the full suite; it is skipping 38 files
for a reason that stopped being true on 2026-08-03. At 112 s the cost is trivial.

This is the genuine value left in TAP-5459 — not fixing failures, but closing the
coverage gap that let TAP-2727's KG status-code drift sit on `main` (the exact
justification written into that CI comment).

### 6.2 Prior finding, now explained

## 6.3 Original triage notes

A review of the 25 open tapps-brain issues turned up a state problem that matters more
than the supersede work.

**Four P1 issues appear stale or mis-specified:**

| Issue | Title | Finding |
|---|---|---|
| TAP-5461 | profile tool count assertion | CHANGELOG says fixed in **3.28.2**. Still Backlog/P1. |
| TAP-5462 | experience_events RLS row visibility | CHANGELOG says fixed in **3.28.2**. `NOSUPERUSER`/`NOBYPASSRLS` probe role confirmed present in tests. Still Backlog/P1. |
| TAP-5463 | docs_postgres cache state divergence | **Mismatch.** The 3.28.2 CHANGELOG cites TAP-5463 for the `doc_memory_key` colon bug — and that fix *is* in `docs_lookup.py:147-153`, citing TAP-5463 in its docstring. But the Linear body describes `test_postgres_observability.py::test_cache_coherence`, **a test that does not exist in the repo**. Either the CHANGELOG cited the wrong number or the issue body was never the real problem. |
| TAP-5460 | `roles/001_db_roles.sql` SQL ordering trap | Not named in any CHANGELOG. Likely genuinely open — needs verification. |

TAP-5461/5462/5463 are all children of **TAP-5459** ("re-enable DB-dependent integration
tests"), and their bodies share a machine-generated shape: templated titles
(`test_*.py: fix ...`), generic acceptance checklists, and at least one reference to a
non-existent test. They read as bulk-filed audit stubs rather than diagnosed defects.

**Why this is the urgent item:** four P1s that are done-but-open, or that point at code
that does not exist, make P1 meaningless. Anyone picking work off this backlog by
priority starts with issues that cannot be actioned. That is a worse day-to-day cost
than the supersede behaviour, which has a config workaround.

### Recommended actions

1. **Close TAP-5459 and all nine children as obsolete** (TAP-5460–5468), with a comment
   recording the measurement: 685 passed / 0 failed, fixed by `938b8d2`. Do not work
   them. Do not "fix" a failure that does not reproduce.
2. **Wire the full integration suite into CI** — replace the 18-file allowlist with
   `tests/integration/`. This is the one piece of real value in the tree.
3. Correct the stale `ci.yml:110` comment in the same change.
4. TAP-5460 is the only one worth a second look: re-file it from the real file
   (`migrations/roles/001_db_roles.sql`) if an ordering trap genuinely exists, or close
   it with the others. Its cited path never existed.
5. **Treat the filing mechanism as the defect.** Ten issues, four at P1, with fabricated
   paths and a 500-char cap, is a process failure that will recur. Find what filed them
   before it files another batch.

## 7. Sequence

| # | Step | Blocking on |
|---|---|---|
| 1 | Tell the consumer about the `per_tier` knob (§3.2) | — |
| 2 | Close the TAP-5459 tree as obsolete, with the measurement recorded | — |
| 3 | Run `tests/integration/` in CI; fix the stale comment | §2 |
| 4 | Implement §3.1 + §3.3, land in 3.29.0 | 3.29.0 not yet tagged |
| 5 | Identify what filed the fabricated stubs | — |
