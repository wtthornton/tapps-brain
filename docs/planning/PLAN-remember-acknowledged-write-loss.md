# Implementation plan — `/v1/remember` acknowledges writes it does not persist

**Status:** proposed, 2026-08-04
**Source report:** `/home/wtthornton/NewCompanyIdeas/docs/cross-project/PROMPT-brain-remember-write-loss.md` (nlt-ideas-scout, tapps-brain 3.28.2)
**Verdict:** report confirmed. All three symptoms are real, reproduce deterministically in-process on `main`, and are **not** concurrency bugs.

---

## 1. Diagnosis

The reporter attributed the failures to concurrency and clock resolution. That is
wrong, and usefully wrong: all three symptoms reproduce with a **single-threaded,
sequential** caller and no clock collision. They are two independent defects on
the save path, both reachable from any single write.

### 1.1 Root cause A — dedup is key-blind (symptoms 2 and 3)

`MemoryStore._handle_dedup` (`src/tapps_brain/store.py:1933-1970`) matches on the
**normalized value only**, scanning every entry regardless of key:

```python
for existing in self._entries.values():
    if normalize_for_dedup(existing.value) == normalized:
        dup_key = existing.key
        break
if dup_key is not None:
    return self.reinforce(dup_key)      # <-- returns a DIFFERENT entry
```

When the incoming key differs from the matched key, the requested write is
discarded and *another* entry is returned. `_save_result_envelope`
(`src/tapps_brain/services/memory_service.py:1007-1023`) then serialises that
foreign entry into the success envelope:

```python
return {"status": "saved", "key": result.key, ...}
```

so the caller receives `200 {"status": "saved", "key": "<someone else's key>"}`.
That single line produces both the stale key echo (symptom 2) and the silently
dropped write (symptom 3) — they are one bug, not two.

Reproduced verbatim (in-process, `InMemoryPrivateBackend`, three sequential
saves of the same value under three distinct keys):

```
ENVELOPES: [{'status': 'saved', 'key': 'diag-echo-0', ...},
            {'status': 'saved', 'key': 'diag-echo-0', ...},
            {'status': 'saved', 'key': 'diag-echo-0', ...}]
GET diag-echo-1: None
GET diag-echo-2: None
```

This is byte-for-byte the reporter's output. The reporter's "1.5 s sleep makes
it pass" observation is a confound — value-scoped dedup is time-independent; the
passing variant almost certainly varied the payload as well as the delay.

`dedup=True` is the default on `MemoryStore.save` (`store.py:1256`) and neither
the HTTP route nor `memory_service.memory_save` overrides it, so **every**
`/v1/remember`, `/v1/remember:batch`, `brain_remember` (MCP) and
`AgentBrain.remember()` call is exposed. `save_many` shares the same pre-persist
pipeline; the behaviour is currently pinned by
`tests/unit/test_save_many.py:113-123`.

### 1.2 Root cause B — reviving an invalidated key violates the bitemporal invariant (symptom 1)

Save-time conflict detection (`_handle_conflicts`, `store.py:1972-2044`) stamps
`invalid_at = T1` on any entry whose value is similar above the profile cutoff.
On a later save of *that same key*, `_construct_memory_entry`
(`store.py:2189, 2226-2227`) builds the new row with:

- `valid_at  = conflict_valid_at` — a fresh `T2` from the new conflict pass, and
- `invalid_at = preserved["invalid_at"]` — the stale `T1` carried off the existing row.

Since `T1 < T2`, the `MemoryEntry` model validator (`models.py:435-444`) raises
`invalid_at must be after valid_at.`, `memory_save` catches the pydantic error
and returns `{"error": "bad_request", ...}`, and the route maps it to **400**.

Reproduced (pattern tier, cutoff 0.6):

```
memory_save_conflicts_detected conflicting_keys=['dossier-a'] similarity=0.8154
A.invalid_at 2026-08-04T22:50:29.842390+00:00
R3 (resave of invalidated key):
  {'error': 'bad_request', 'detail': 'Value error, invalid_at must be after valid_at.'}
```

The error string matches the report exactly. No concurrency, no clock collision,
no timestamp fields in the request — the interval is built entirely server-side
from state the caller cannot see. It is state-dependent, which is why the
reporter's replay of the same bytes returned 200 (by then the conflicting
partner had been archived by gc, so no new `conflict_valid_at` was produced).

`repo-brain` — the default profile for any project without its own — sets
`conflict_check.per_tier.context: 0.85` (`profiles/repo-brain.yaml:108-111`).
nlt-ideas-scout writes ~3.2 KB JSON dossiers at `tier: context` that share a
fixed schema skeleton, which is exactly the shape that clears an 0.85 Jaccard
cutoff without being a contradiction.

### 1.3 Collateral finding — silent invalidation is invisible to the caller

Every conflict hit sets `invalid_at` on the *other* entry, which removes it from
recall (`invalid_at > now()` predicate, `_postgres_private_sql.py:326`) and marks
it `contradicted` so gc can archive it (`contradicted_threshold: 0.2`). Two
similar-but-both-correct dossiers therefore delete each other from retrieval,
and nothing in either response says so. This is not in the report, but it is the
same class of defect: a durable side effect the acknowledgement hides.

### 1.4 Verdict on the reporter's three asks

| Ask | Response |
|---|---|
| 1. Confirm serialisation / clock-collision under concurrency | Not the cause. The interval is not derived from a colliding clock; it is derived from a stale `invalid_at` preserved across a revive. Concurrency is not required to reproduce. |
| 2. Treat the stale key echo as a correctness bug | Agreed, and it is the *same* bug as the dropped write. |
| 3. Never return `200 {"status":"saved"}` for a write that did not persist | Agreed without reservation. Fixed structurally (§2.3) so no future coalescing path can re-open it. |

The reporter's decision not to add a retry, sleep, or read-after-write verify on
the consumer side is correct. No consumer-side change is required by this plan.

---

## 2. Fix

Four changes, in dependency order. Each is independently shippable.

### 2.1 Scope dedup to the incoming key — `store.py`

Replace the value-only cross-key scan in `_handle_dedup` with an O(1) same-key
check:

```python
def _handle_dedup(self, key: str, value: str, dedup: bool) -> MemoryEntry | None:
    """Same-key no-op fast path.

    A re-save of an unchanged value under the SAME key is a genuine no-op and
    short-circuits to a reinforce. A matching value under a DIFFERENT key is a
    distinct memory identity and must persist — collapsing it destroyed key
    addressability and returned a foreign entry to the caller (TAP-<id>).
    """
    if not dedup:
        return None
    normalized = normalize_for_dedup(value)
    with self._serialized():
        existing = self._entries.get(key)
        self._bloom.add(normalized)
    if existing is not None and normalize_for_dedup(existing.value) == normalized:
        self._metrics.increment("store.save.dedup_skip")
        try:
            return self.reinforce(key)
        except KeyError:
            return None
    return None
```

Notes:

- Drops the `_merge_durable_entries()` round-trip and the O(n) scan from the hot
  path. A cold-start store that has not merged durable rows will miss the
  same-key no-op and fall through to a normal upsert — correct, just not free.
- The bloom filter keeps being fed (`gc()` rebuilds from it); it is no longer
  consulted on the save path.
- **Verify before merging:** `ingest_context` / `extraction.py` derive keys from
  content, so identical content still yields an identical key and still
  short-circuits. Confirm with `tests/unit/test_extraction.py` and any ingest
  integration test before assuming no row-count regression.

### 2.2 Clear the stale interval when a key is revived — `store.py`

In `_construct_memory_entry`, once `effective_valid_at` is computed
(`store.py:2189`), never construct an entry that violates the model invariant:

```python
effective_valid_at = conflict_valid_at or preserved["valid_at"]
effective_invalid_at = preserved["invalid_at"]
# A row that was invalidated at T1 and is being rewritten with a new validity
# start T2 > T1 is being REVIVED: the old closing bound belongs to the previous
# version of the row, not this one. Carrying it forward violates the
# valid_at < invalid_at invariant and surfaced as a caller-facing 400 on a
# request that carried no temporal fields at all (TAP-<id>).
if (
    effective_invalid_at is not None
    and effective_valid_at is not None
    and _parse_iso(effective_invalid_at) <= _parse_iso(effective_valid_at)
):
    effective_invalid_at = None
    superseded_by = None
    preserved["contradicted"] = False
    preserved["contradiction_reason"] = None
```

Resetting `contradicted` / `contradiction_reason` / `superseded_by` alongside is
required, not cosmetic: leaving them set keeps the revived row eligible for gc
archival under `contradicted_threshold`, which would re-create the write-loss
symptom through a different door.

### 2.3 Make the envelope structurally incapable of lying — `memory_service.py`

`_save_result_envelope` currently reads the key off whatever entry it was
handed. Pass the requested key and assert identity:

```python
def _save_result_envelope(result: Any, *, requested_key: str | None = None) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    envelope = {
        "status": "saved",
        "key": result.key,
        "tier": str(result.tier),
        "confidence": result.confidence,
        "memory_group": result.memory_group,
    }
    if requested_key is not None and result.key != requested_key:
        # Any coalescing path (dedup, write-policy UPDATE redirect) must say so
        # rather than echoing a foreign key under "saved".
        envelope["status"] = "coalesced"
        envelope["key"] = requested_key
        envelope["coalesced_into"] = result.key
        envelope["persisted"] = False
    return envelope
```

- `"deduplicated"` already exists as a status token in `session_summary.py:183`;
  `"coalesced"` is the broader term here because the write-policy `UPDATE`
  redirect (`_prepare_save` → `effective_key`, `store.py:1487-1499`,
  LLM-write-policy mode only) reaches the same envelope with the same lie.
- After §2.1 this branch should be unreachable for dedup. It stays as a
  structural guarantee: **no future coalescing path can silently claim
  `"saved"`.** That is the durable answer to the reporter's ask #3.
- Thread `requested_key` through both call sites: `memory_save` and
  `memory_save_many` (`memory_service.py:1004`, `:1238+`).

### 2.4 Surface conflict invalidation in the response — `store.py`, `memory_service.py`

When a save invalidates other entries, return their keys:

```python
{"status": "saved", "key": ..., "invalidated": ["other-key-1", ...]}
```

`_handle_conflicts` already has `plan.conflict_keys`; it currently only reaches a
`logger.warning`. Plumb it onto the returned entry (or a parallel out-param) and
into the envelope. Callers cannot currently observe that a write deleted a
neighbour from recall.

Optional follow-up, not part of this fix: revisit whether "high lexical
similarity" should imply "contradicted", or whether it warrants a weaker
`related` marker that does not close the validity interval. That is a design
question; file it separately rather than widening this change.

---

## 3. Tests

New file `tests/unit/test_save_write_loss.py`:

1. `test_same_value_distinct_keys_all_persist` — the reporter's five-key probe;
   assert five distinct keys readable, five envelopes echoing their own key.
2. `test_same_key_same_value_is_reinforce_noop` — dedup still short-circuits;
   `reinforce_count` increments; no duplicate row.
3. `test_resave_of_invalidated_key_returns_200` — the §1.2 sequence
   (save A, save similar B at pattern tier, re-save A) returns `status: saved`
   with `invalid_at is None`, `contradicted is False`.
4. `test_revived_entry_is_recallable` — the revived row comes back from
   `recall()` (guards the gc/archive door in §2.2).
5. `test_envelope_never_claims_saved_for_foreign_key` — call
   `_save_result_envelope` directly with a mismatched entry; assert
   `status == "coalesced"` and `persisted is False`.
6. `test_conflicting_save_reports_invalidated_keys` — §2.4.

HTTP-level, in `tests/unit/test_http_adapter.py`:

7. `test_v1_remember_distinct_keys_same_value` — TestClient, five POSTs, then
   five `GET /v1/get` (or `/v1/forget`) confirming each key exists. This is the
   test that would have caught the reported defect at the API boundary.
8. Batch parity: same assertion through `/v1/remember:batch`.

Existing tests to update:

- `tests/unit/test_save_many.py:113-123`
  (`test_dedup_hit_returns_entry_without_rebatching`) pins the old cross-key
  behaviour. Rewrite as `test_dedup_hit_same_key_returns_entry_without_rebatching`
  and add a distinct-key case asserting both rows persist.
- Grep for any other assertion on `store.save.dedup_skip` or on `get(<dup key>)
  is None` before merging.

Gate: `pytest tests/ -m "not benchmark" --cov-fail-under=95`, `ruff check`,
`ruff format --check`, `mypy --strict src/tapps_brain/`.

---

## 4. Rollout

1. Land §2.1–§2.3 together — they are one caller-visible contract.
2. `MIGRATE=0 make dev-deploy` (no schema change; the revive path only rewrites
   column values that already exist).
3. Re-run the reporter's exact repro against the live instance at
   `http://127.0.0.1:8080` and paste the output into the response document.
4. Patch release `3.28.3`; `bash scripts/release-ready.sh`, then build the wheel
   from a worktree at the tag (not from `main`).
5. Reply to nlt-ideas-scout at
   `docs/cross-project/PROMPT-brain-remember-write-loss.RESPONSE.md` — see §5.
6. Sweep for existing damage: any project whose writes were coalesced has rows
   under the wrong key. There is no server-side record of the requested key
   (that is what was lost), so recovery is consumer-side re-write only. Say so
   plainly rather than implying a repair path exists.

**Backfill note for nlt-ideas-scout specifically:** their dossiers were written
at `tier: context` with distinct sha256 keys and distinct values, so §1.1 is
unlikely to have coalesced them; their loss vector is the §1.2 400s (7 of 24
observed), which never persisted at all and are simply re-writable on the next
poll tick once 3.28.3 is deployed.

---

## 5. Consumer response

Write `PROMPT-brain-remember-write-loss.RESPONSE.md` alongside the report,
covering: symptoms confirmed; concurrency and clock resolution ruled out as
causes; the two actual root causes with `file:line`; the fix and the release it
lands in; confirmation that no consumer-side retry/sleep/verify is wanted; and
the note that their "1.5 s sleep fixes it" observation was a confound.

---

## 6. Linear

Filed 2026-08-04 in `TappsCodingAgents` / `tapps-brain`, all Urgent, all
assigned to Claude Agent:

- **TAP-5614** (epic) — `/v1/remember` acknowledges writes it does not persist
- **TAP-5615** — `store.py`: dedup discards writes under distinct keys (§2.1, tests 1/2/5/7/8)
- **TAP-5616** — `store.py`: reviving an invalidated key returns spurious 400 (§2.2, tests 3/4)
- **TAP-5617** — `memory_service.py`: save envelope hides coalesced and invalidated writes (§2.3, §2.4, tests 5/6)

TAP-5615 and TAP-5617 ship together — they are one caller-visible contract change.
