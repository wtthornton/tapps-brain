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

## 6. Backlog triage — the more urgent finding

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

1. Close TAP-5461 and TAP-5462 — verified shipped in 3.28.2.
2. **Do not** blind-close TAP-5463. Decide which of the two problems it names is real:
   re-point the issue at the shipped `doc_memory_key` fix and close, or keep it open for
   the cache-coherence concern and correct the body to name a test that exists.
3. Verify TAP-5460 against `migrations/roles/001_db_roles.sql` before deciding.
4. Audit the rest of the TAP-5459 children (TAP-5464 through TAP-5468) for the same
   stub pattern before anyone schedules them.

## 7. Sequence

| # | Step | Blocking on |
|---|---|---|
| 1 | Tell the consumer about the `per_tier` knob (§3.2) | — |
| 2 | Close TAP-5461, TAP-5462; triage TAP-5463, TAP-5460 | — |
| 3 | Implement §3.1 + §3.3, land in 3.29.0 | 3.29.0 not yet tagged |
| 4 | Audit TAP-5464..5468 for the stub pattern | §2 |
