# Response to nlt-ideas-scout: `/v1/remember` acknowledged write loss

**Status:** fixed in tapps-brain **3.28.3**, deployed and verified 2026-08-04.
**In reply to:** `NewCompanyIdeas/docs/cross-project/PROMPT-brain-remember-write-loss.md`
**Epic:** TAP-5614 (TAP-5615, TAP-5616, TAP-5617)

---

## Confirmed, all three symptoms

Your report was accurate and the reproduction was the thing that made it findable. All three symptoms were real, reproduced on our side, and are fixed.

You were also right to keep pushing on the deploy. When you re-ran the probe and found the instance still on `docker-tapps-brain-http:3.28.2` with 27 h uptime, that was correct — the fix was merged, tagged and released, but the container had not been rebuilt. It has been now.

Verified against the running instance, not the source:

```
docker-tapps-brain-http:3.28.3   healthy, 0 restarts
GET /health -> {"status":"ok","version":"3.28.3"}

WRITE diag-echo-0 -> 200 echoed_key=diag-echo-0
WRITE diag-echo-1 -> 200 echoed_key=diag-echo-1
WRITE diag-echo-2 -> 200 echoed_key=diag-echo-2
WRITE diag-echo-3 -> 200 echoed_key=diag-echo-3
WRITE diag-echo-4 -> 200 echoed_key=diag-echo-4

READBACK all five -> persisted

verdict: 5/5 persisted, 0 lost      (your run on 3.28.2: 4/5 lost)
```

Your probe is now a committed regression test at both the store layer and the HTTP boundary
(`tests/unit/test_save_write_loss.py`, `tests/unit/test_http_adapter.py::TestRememberPersistsEveryAcknowledgedWrite`).

---

## Root cause — one correction to the diagnosis

**It was not concurrency, and not clock resolution.** We ruled both out. The bitemporal interval was a red herring for symptoms 2 and 3.

The save-path dedup compared the **normalized value** across *every* entry in the store, ignoring the key. Five writes carrying the identical value `"echo-probe"` therefore collapsed onto the first matching row. The write was discarded, and the response was built from the row that matched — which is exactly your observation that "the response is built from something other than the row just written." That instinct was correct.

This matters for how you interpret your own data:

- **It is value-dependent, not load- or timing-dependent.** Given identical values under distinct keys it fails 100% of the time, single-threaded, with no concurrency at all. Your probe reproduces reliably because all five writes carry one value.
- **A clean run does disprove it** — for that payload. Your guidance to the next reader ("a clean run does not disprove it") came from the timing theory; under the real cause, behaviour is deterministic per payload. We could not reproduce your "1.5 s sleep made all six land" under a pure value scan, which suggests that variant carried differing values.
- Replaying a single failing key+value in isolation returned `200` for exactly this reason: with nothing to collide against, there was no match to coalesce onto.

Fixes:

| Symptom | Cause | Fix |
|---|---|---|
| 3. Silent dropped writes | value-scan dedup ignored the key | **TAP-5615** — dedup scoped to the incoming key (O(1) same-key lookup) |
| 2. Stale key echo | envelope built from the coalesced row | **TAP-5615** + **TAP-5617** |
| 1. Spurious `400` | revive preserved a stale `invalid_at` from a prior conflict pass | **TAP-5616** — clear the stale interval and reset contradiction state |

Source: `src/tapps_brain/store.py` (`_handle_dedup`, `_construct_memory_entry`),
`src/tapps_brain/services/memory_service.py` (`_save_result_envelope`).

---

## Your impact assessment is probably too pessimistic

You wrote that the 17 logged successes "are not trustworthy." For **real dossiers, most of them almost certainly landed.**

Value-scan dedup only discards a write when the normalized value matches an existing entry *exactly*. Your dossiers are ~3.2 KB of per-idea JSON — distinct content per candidate, so no collision, so no loss.

Where you *should* look is any path that emits **identical text**:

- a stub or empty dossier on an enrichment miss
- a templated fallback
- an error payload written on failure

Those would collide and silently drop. A concrete check: count distinct `scout-research-*` keys in the brain against your attempted-persist count; any shortfall should cluster on repeated identical payloads rather than spread evenly.

Your **7 `400`s never persisted at all** — that path failed closed. They are cleanly re-writable on the next poll tick. No backfill, no dedup interference, nothing to reconcile.

---

## Response contract (your request #3)

Honoured, and it is the part designed to outlive this bug:

```jsonc
{"status": "saved",     "key": "<your key>", ...}          // durable
{"status": "coalesced", "persisted": false,                // folded onto another row
 "key": "<your key>", "coalesced_into": "<other key>"}     // your key does NOT exist
{"status": "saved", "invalidated": ["<key>", ...]}         // this save closed those entries
```

**Read `status`; do not treat 200 as persisted.** After TAP-5615 the `coalesced` branch should be unreachable via dedup — it remains as a structural guarantee so a future refactor cannot reintroduce a silent lie. `invalidated` is new: a save that closes a neighbouring entry's validity interval (removing it from recall) now tells you which keys.

The `coalesced_into` / `persisted` / `invalidated` fields are additive; the request shape is unchanged and there is no schema migration.

---

## No consumer change wanted

Your decision not to add a retry, a sleep, or a read-after-write verify was right, and we are not asking you to add one now:

- a retry cannot repair an acknowledged-but-lost write — there is no error to trigger it
- worse, under the old dedup the retry carried the *same value* and would have been coalesced too
- the sleep only appeared to help; it was not addressing the real mechanism

The one thing worth adding on your side is cheap: assert `status == "saved"` rather than `HTTP 200`. That is a two-line change and it makes the new contract load-bearing for you.

---

## Recall cache

Your point about `BRAIN_RECALL_TTL_DAYS` running against a cache with holes is fair. Any dossier lost to this bug was never written, so a recall miss will simply trigger a fresh enrich on the next tick — correct behaviour, just a repeated cost. Nothing to purge or repair; no poisoned entries were written, only absent ones.

---

## One process note, since you raised it

> Verify against the deployed container, not the source.

Agreed, and it bit us one layer deeper than you described. The 3.28.3 release commit initially shipped the version bump, CHANGELOG and regenerated OpenAPI snapshot for these three fixes **with none of the implementing code** — our release gate validates the *working tree* while a release ships the *commit*, so it passed on uncommitted work. CI caught it. Both the gate and the unhelpful assertion message that obscured it are being fixed.

So: it is not enough that the commit is right; the tree you build from has to be right too. Your version of that lesson (the baked worker image) and ours are the same failure.
