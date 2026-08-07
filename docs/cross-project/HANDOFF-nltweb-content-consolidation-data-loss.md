# Handoff — nltweb-content: auto-consolidation destroyed three research entries

**From:** tapps-brain (service owner)
**To:** whoever owns `nltweb-content`
**Date:** 2026-08-06
**Status:** brain-side defect **fixed and deployed** (3.31.0). Your data has been
**restored**. One optional configuration decision is left to you — see
[What is yours to decide](#what-is-yours-to-decide).

---

## What happened to your data

On 2026-08-07 00:39 UTC, three `architectural` entries you had just saved were
auto-consolidated into a single generated key:

| key | chars |
|-----|-------|
| `linkedin-publish-ai-policy-2026` | 3790 |
| `linkedin-publish-api-access-2026` | 3395 |
| `linkedin-publish-vendor-options-2026` | 2829 |
| **merged into** `linkedin-publish-c6c49bb0` | **4096** (capped) |

10,014 chars of sources became a 4,096-char summary — **29.7% retained**. The
three originals were marked `contradicted=true` with `superseded_by`, which does
not delete them but *does* remove them from semantic recall. An exact
`memory_get` still returned them; `brain_recall` did not. Your own pointer entry
diagnosed this correctly at the time.

The merge fired because the entries shared tags, not because they were similar.

## Why it happened (brain-side defects — both now fixed)

1. **`should_consolidate` never consulted the similarity threshold on the
   same-topic path.** `is_same_topic` is only "same tier AND ≥50% tag overlap of
   the smaller set" — no text signal at all. Three entries sharing
   `linkedin`/`publishing`/`content` tags matched pairwise regardless of content.
   `min_entries` defaults to 3, so the merge fired on the third save. **There was
   no time window and no write-rate signal** — the fact that your three saves
   landed close together was coincidence, not the trigger.
2. **`_extract_sentences` split on bare `[.!?]+` and lowercased every fragment.**
   That is what turned `learn.microsoft.com` into `['…official learn',
   'microsoft', 'com docs)']` — and since `microsoft` was already seen, the merge
   emitted the mangled `official learn com docs)`.

This was the **save path only**. The periodic scan uses a real threshold and
never had the bypass.

## What tapps-brain 3.31.0 changed

Three independent guards, any one of which stops your case:

- **Threshold AND-gate** — same topic is now a *necessary* condition, never a
  sufficient one.
- **Content-preservation floor (0.6)** — a merge retaining <60% of its sources'
  summed bytes is refused before any write. Your case was 29.7%. Refusals
  increment `store.consolidate.blocked_content_loss` and log
  `auto_consolidation_blocked_on_save`.
- **`architectural` tier exempt by default** — architectural entries no longer
  enter the consolidation check at all.

Also added, and relevant to you:

- `brain_remember(..., skip_consolidation=True)` — save a self-contained artifact
  that must never be merged.
- `brain_recall(..., include_sources=True)` — read back the originals behind any
  existing merge.
- `document_put` / `document_get` / `document_search` / `document_list` are now on
  the `coder` and `agent_brain` profiles. **Long artifacts belong there**, not
  split across `memory_save` calls — memory values are capped at 4096 chars, and
  a long document chopped into same-tagged entries is exactly the shape that got
  merged.

## What was already done to your data

Run by the tapps-brain operator on 2026-08-06, with a full backup taken first:

1. `maintenance consolidation-merge-undo linkedin-publish-c6c49bb0` — restored the
   three sources (`contradicted=false`, `superseded_by=NULL`, `status=active`) and
   deleted the consolidated row.
2. **Your pointer entry was preserved.** The undo deletes the consolidated row,
   and by then that key no longer held merge output — it held the
   `linkedin-publish-record-pointer` entry your agent wrote at 02:02/02:04 to
   document the incident. It was extracted before the undo and re-saved under the
   **same key** with the same tier, tags and confidence.

Final state — all four rows active, all four **SHA-256 identical** to the
pre-undo backup:

```
linkedin-publish-ai-policy-2026        3790  active
linkedin-publish-api-access-2026       3395  active
linkedin-publish-c6c49bb0 (pointer)    2972  active
linkedin-publish-vendor-options-2026   2829  active
```

Semantic recall now returns the originals. **Nothing was lost.**

> Your pointer entry's text still says the originals "carry contradicted=true".
> That is no longer accurate. It was restored verbatim rather than edited,
> because editing another project's memory content is not the service owner's
> call. Update or retire it as you see fit.

## What is yours to decide

**Nothing is required.** 3.31.0 protects you by default.

A profile override disabling consolidation entirely for `nltweb-content` was
briefly registered and then **deliberately reverted** — it duplicates a guarantee
the code now makes, and per-tenant retrieval policy belongs to the tenant, not to
the service owner. Your project is back on defaults.

If you *do* want belt-and-braces (reasonable — you write long-form research under
shared tags), register it yourself:

```bash
# 1. Start from the built-in repo-brain profile
#    src/tapps_brain/profiles/repo-brain.yaml in the tapps-brain repo
#
# 2. Add this block under `profile:` (2-space indent, sibling of `limits:`)
#
#    consolidation:
#      enabled: false
#      threshold: 0.7
#      min_entries: 3
#      exempt_tiers: [architectural, pattern, procedural, context]
#
# 3. Register it (the CLI must run INSIDE the container — the DSN host
#    `tapps-brain-db` does not resolve from the host machine)

docker cp your-profile.yaml tapps-brain-http:/tmp/p.yaml
docker exec tapps-brain-http tapps-brain project register nltweb-content \
  -p /tmp/p.yaml --approved --source admin --notes "why you did this"
make hive-reload-http
```

Two things that cost time when we did it, so you do not repeat them:

- `ConsolidationProfileConfig` is `extra="forbid"`, and `ProfileRegistry` fails
  **at server startup** on drift. A typo in that block is a boot failure, not a
  test failure. Validate first:
  `MemoryProfile.model_validate(yaml.safe_load(open(p))["profile"])`.
- Any CLI command touching your data needs **both** `TAPPS_BRAIN_PROJECT=nltweb-content`
  **and** `TAPPS_BRAIN_AGENT_ID=<your agent id>`. Private memory is keyed by
  `(project_id, agent_id, key)`. With the wrong agent the CLI reports "no
  consolidation merge audit found", which reads like missing data when it is
  really a scoping miss.

## Recommended, cheap, entirely yours

Move the three research bodies to the **document plane** (`document_put`) and keep
short pointer entries in memory. Memory values are capped at 4096 chars; these
are 2.8k–3.8k each and growing. The document plane is built for exactly this and
is not subject to consolidation at all.

---

# Second, unrelated defect found in your data — save-time conflict detection

While verifying the above we found a **different** mechanism hiding entries of
yours. It is not consolidation and the 3.31.0 fix does nothing for it.

**12 of your 34 entries (35%) were invisible to `brain_recall`.** They carry
`contradicted=true` with reason `Save-time conflict: invalidated by incoming
memory ...`. `retrieval.py:516` drops contradicted entries; the content was never
deleted, but an exact `memory_get` was the only way to reach it.

## Cause — a tapps-brain default, not your configuration

Your `exec-nltweb-content-post-strategist-*` entries are ~4,094-char agent
execution transcripts saved to the `procedural` tier. TF-cosine over long,
structurally-similar documents converges on document **shape** rather than
subject, so entries about entirely different topics cleared the 0.6 cutoff.
Measured on your live corpus:

| | count | similarity |
|---|---|---|
| cross-topic — should NOT conflict | **7** | 0.6026 – 0.7254 |
| same-topic duplicates — correctly caught | 5 | 0.7324 – 0.8862 |

`repo-brain` had already raised `context` to 0.85 for this exact reason
(TAP-4464) and simply never gave `procedural` the same treatment. You were on
stock defaults, so **nothing you configured caused this.**

## Fixed in tapps-brain 3.31.1 — no action needed from you

`repo-brain.yaml` now sets `conflict_check.per_tier.procedural: 0.75`, above
every measured false positive. Short-bodied tiers (`architectural`, `pattern`)
keep 0.6 — this is not a blanket raise. Embedding cosine was evaluated as an
alternative signal and rejected: it separated *worse* (0.05 margin vs
TF-cosine's 0.19).

**New saves are protected.** One true duplicate at 0.7324 will now survive
instead of being invalidated — deliberate, because a surviving duplicate is
visible and supersedable while a false invalidation is not.

## The 7 already-hidden entries

Restoring an existing conflict invalidation needs a direct DB write — there is no
supported undo (tracked as TAP-5782; `consolidation-merge-undo` covers merges
only, and re-saving preserves the flag via `store.py:346`). Ask the tapps-brain
operator to clear `contradicted` on these 7 keys; the other 5 are genuine
duplicates and should stay:

```
exec-nltweb-content-post-strategist-produce-a-brief-for-one-nlt-labs-8aa39833432c
exec-nltweb-content-post-strategist-topic-agent-observability-matte-585e59c675d9
exec-nltweb-content-post-strategist-topic-agent-observability-matte-fd198c23f0a7
exec-nltweb-content-post-strategist-topic-engineering-leaders-shoul-257e45a315be
exec-nltweb-content-post-strategist-topic-engineering-leaders-shoul-345a2e113481
exec-nltweb-content-post-strategist-topic-topic-seed-in-agentforge-855384ad078e
exec-nltweb-content-post-strategist-workflow-node-strategist-topic-8a8d8b22eb6b
```

## Same recommendation, stronger

These transcripts are the clearest case yet for the **document plane**. At ~4,094
chars they sit against the 4,096 cap, they are archival rather than recallable
facts, and `document_put` has neither conflict detection nor a length cap. Both
defects in this handoff — the consolidation merge and the conflict invalidation —
came from long-form artifacts living in the memory plane.

## Related tracking

- TAP-5782 — no supported undo for save-time conflict invalidation
- TAP-5783 — `include_contradicted` unreachable from `brain_recall`, so hidden
  entries cannot even be listed without SQL

## References

- Fix: tapps-brain `9a0d09e` (release 3.31.0), PR #263
- Trigger mechanics: `docs/engineering/call-flows.md` → "Auto-consolidation trigger (save path)"
- Tier guidance: `CLAUDE.md` → "Long artifacts go to the document plane"
- Pre-undo backup of all four rows was taken by the operator; ask them for it if
  you want to diff against the pre-restore state.
