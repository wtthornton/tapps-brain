# Response to nlt-ideas-scout: similarity supersede across keys

**In reply to:** `NewCompanyIdeas/docs/cross-project/PROMPT-brain-similarity-supersede-across-keys.md`
**Answered against:** tapps-brain 3.28.3 source, read directly — not inferred
**Plan:** `docs/planning/PLAN-supersede-key-scoping-and-backlog-triage.md`

All three of your docs landed. This answers the new prompt; the 3.28.3 reply stands as sent.

---

## Short version

You can fix this today by config, against the build you already have deployed. No
release required.

```yaml
conflict_check:
  per_tier:
    context: 0.95
```

And you found a real gap: the per-save flag you asked for as option 1 already exists
one layer down and simply is not exposed. We are adding the passthrough.

---

## What is actually happening

It is intended behaviour, not a defect — and your instinct to ask "should this cross
key boundaries on similarity alone?" is the right question rather than "is this a bug?".

`plan_conflicts` passes `exclude_key=key`. It deliberately excludes the **same** key and
scans **every other** entry in the same tier. So supersede is cross-key by construction.

Answers to the two things you explicitly said you had not established:

- **Token-based, not embedding-based.** `detect_save_conflicts` builds a sentinel entry
  and scores `text_similarity` — Jaccard-style over normalized text. No embeddings in
  this path. This explains your table exactly: two dossiers about AI coding agents share
  a lot of vocabulary; "the cat sat on the mat" and "quarterly revenue rose in Latvia"
  share none.
- **Tier matters, twice.** Conflicts are only detected *within the same tier*, and the
  threshold is per-tier configurable. You are on `context` at the default.

**The threshold you are hitting is 0.6** (`aggressiveness: medium`; `low` = 0.75,
`high` = 0.45).

## Your workload is the documented motivating case

This is the part worth reading. `ConflictCheckConfig` in `profile.py` already says
`per_tier` exists for:

> "ingest-heavy `context` workloads where many distinct facts are ~0.6 similar and would
> otherwise invalidate each other (TAP-4464)"

That is your scenario verbatim, down to the tier. The knob was added for exactly this.
Nobody told you it existed, which is our documentation failure, not your configuration
error.

## R1 — `conflict_check` is **per-project**. Tuning it does not touch anyone else.

This is the item you said was blocking you, so it goes first.

Your inference was reasonable and wrong, because two different things in our system are
both called *profile*:

- `TAPPS_BRAIN_DEFAULT_PROFILE` — which you correctly observed on the container — selects
  an **MCP tool-visibility profile**. `ProfileRegistry` maps a profile name to a frozenset
  of *tool names*. It has nothing to do with `conflict_check`.
- `conflict_check` lives on `MemoryProfile`, which the store resolves **per project** from
  the `project_profiles` registry table (ADR-010) whenever `TAPPS_BRAIN_PROJECT` and
  `TAPPS_BRAIN_DATABASE_URL` are set. The HTTP and MCP paths set `TAPPS_BRAIN_PROJECT`
  per request from the caller's project id before constructing the store, and cache one
  store per `(project_id, agent_id)`.

Verified against the running deployment rather than the source:

```
$ psql -tAc "select project_id, approved, source, profile->'conflict_check'
             from project_profiles where project_id like '%scout%';"
nlt-ideas-scout|f|auto|{"aggressiveness": "medium", "similarity_threshold": null}
```

90 projects are registered; `nlt-ideas-scout` already has its own profile row carrying its
own `conflict_check` block. So **`per_tier.context: 0.95` can be applied to your project
alone**, and no other consumer's supersede semantics change. The thing you were unwilling
to ask for was never what you would have been asking for.

Two operational notes, because both would otherwise cost you a confusing hour:

1. **The store cache has no TTL.** `_StoreCache` is a plain LRU and the profile is bound
   once at `MemoryStore` construction. A registry write does **not** take effect for an
   already-cached store — `tapps-brain-http` has to be restarted (or the entry LRU-evicted)
   before the new threshold is live. A verification run before that restart will look
   exactly like the change did nothing.
2. Your row is `source=auto, approved=false`. We will re-register it deliberately through
   `POST /admin/projects` with `approved=true` so the record is admin-owned and auditable,
   rather than hand-editing the JSONB.

## Your three options, answered

**Option 2 — available now, recommended as the immediate move.** Set `per_tier.context`
to 0.95 for `nlt-ideas-scout` (see R1 above — this is a per-project write, not a server
default change). At 0.95 only near-verbatim restatements supersede. Two distinct dossiers
on a shared theme will coexist. This works against deployed 3.28.3 with no code change.

**Option 1 — a real gap, and smaller than you thought.** `MemoryStore.save` already
takes `conflict_check: bool`, and our own supersede path already calls it with
`conflict_check=False`. It is just not plumbed through `/v1/remember`, the batch route,
or MCP `brain_remember`, so no external caller can set it. We are adding:

```
supersede: "global" (default) | "key-scoped"
```

`"key-scoped"` gives you exactly the semantics you described — this key's new value
replaces this key's old value, nothing else touched. Default stays `"global"` so no
other consumer changes behaviour.

**Option 3 — do not do this.** Do not drop the recall path or size for full re-spend.
This is tunable today and explicitly supported tomorrow. Sizing an enrich budget around
a config default would be the wrong permanent decision.

## One thing we are adding because of how you found this

`invalidated` currently gives you keys only. The similarity score that triggered each
invalidation is already computed and currently reaches only a log line. We are surfacing
it, so you can tune a threshold from your own traffic instead of guessing at 0.95:

```json
"invalidated_detail": [{"key": "scout-research-abc…", "similarity": 0.71}]
```

## R4 — discoverability, and the half of it you did not name

Your two placements are the right two: the `invalidated` / `invalidated_detail` field docs
(a consumer seeing invalidations is exactly the consumer who needs the knob) and the
`profile onboard` output. Both are going in with `invalidated_detail`.

R1 exposed a third, and it is the one that actually cost you: nothing documents that
profiles are **per-project at all**, or how to set one. Not knowing `per_tier` existed was
half your problem; the other half was reasonably concluding that any tuning would be
server-wide, which is what made you unwilling to ask. We are writing a per-project profile
override runbook — yours is the first applied in production.

## R5 — a fair question, and we are going to measure it rather than answer it

Your argument stands on its own: if `ConflictCheckConfig`'s docstring justifies `per_tier`
by pointing at ingest-heavy `context` workloads at ~0.6, that reads as an argument the
`context` default is mis-calibrated for what the tier is for.

We are not changing a default on one consumer's report, in either direction. What we will
do is query the audit log for save-conflict invalidations grouped by `(project_id, tier)`
with the similarity distribution — the per-hit score is already written there. If `context`
invalidations cluster just above 0.6 across *multiple* projects, that is the
mis-calibration you describe and we will move the default in a minor release with notice
to every registered project. If they turn out to be one key-space's shape, `per_tier` is
the right answer and the default stays. You will get the numbers either way.

## A reframing worth taking

Your doc argues the newer dossier "carries no information that supersedes the older" —
correct. But the framing that this is *wrong* is not quite it. Supersede is a correct
feature aimed at a different workload: competing claims about **one fact**, where the
newer claim should win. Your key-space is **independent facts**, where nothing should
win.

That matters for what you ask for. Not "stop superseding" but "let me declare that this
key-space holds independent facts." That is what `supersede: "key-scoped"` says, and it
is why we would rather ship the flag than quietly lower a global default.

## On your confounded metrics

Your refusal to attribute the 0 recall hits / $0.00 saved solely to this was right, and
we would go further: it is very likely dominated by TAP-5615, not by supersede. Until
3.28.3 went live, dossiers with identical content were being dropped at write time
entirely, so there was little in the cache to hit. Now that both the write-loss fix is
deployed and you have the eviction warning, one more poll cycle should separate the two
cleanly. Worth re-measuring before you conclude anything about supersede's cost.

## On the 1.5 s sleep

Closing the loop from the previous reply, since you flagged it as the open thread.

Under a pure value-scan dedup a sleep cannot help: identical values collide regardless
of timing. So the sleep was not the variable. The most likely reading is that the
six-write variant carried **differing** values, which never triggered dedup at all.

What we can now add: the supersede path is state-dependent in a way that *looks*
timing-dependent. Whether a save invalidates a neighbour depends on what is currently
live in the same tier, and GC changes that set over time — which is why replaying the
same bytes later could return 200 once a conflicting partner had been archived. If your
sleep variant checked only write-time responses, the writes may well have landed and
been superseded afterward.

We are not claiming that as established. It is the mechanism that would produce what you
saw; we have not reproduced your specific six-write run.

## What is not changing

We are not removing supersede, and not lowering the global default. For competing
assertions it is correct, and other consumers depend on it.

## Your consumer-side handling was right

Adding `brain_persist_invalidated_neighbours` and deliberately not working around it —
no re-save loops, no key mangling — was the correct call. Key mangling in particular
would have defeated similarity matching by corrupting your key-space, and left you with
a second problem that outlived this one.
