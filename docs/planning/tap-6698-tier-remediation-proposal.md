# TAP-6698 / VAL-09 — remediation proposal for the out-of-enum tier rows

**Status:** proposal. **No live data was mutated by this lane.** Every statement
below was established with read-only `SELECT`s against `tapps-brain-db`.
Applying anything here is an SC-6 pass (dry-run → diff → backup table →
operator ACCEPT → apply) that the orchestrator relays.

## What is actually there

Twelve rows, written in a **1.17-second window** on 2026-08-07 22:58:48.903 →
22:58:50.074 UTC, spread across four `project_id`s that appear nowhere else in
the system. All carry `agent_id='default'`, `source='human'`,
`source_agent='unknown'`, empty `tags`, `status='active'`, no `invalid_at`, no
`superseded_by`.

| project_id | key | tier | value |
|---|---|---|---|
| `56281493d732ffce` | `test-identity` | `identity` | User prefers dark mode in all applications |
| `56281493d732ffce` | `test-long-term` | `long-term` | User works at Acme Corp as a software engineer |
| `56281493d732ffce` | `test-short-term` | `short-term` | Currently working on the Q4 report |
| `56281493d732ffce` | `test-ephemeral` | `ephemeral` | Just asked about the weather in NYC |
| `4377796229cb4682` | `id-1` | `identity` | User name is Alice |
| `4377796229cb4682` | `lt-1` | `long-term` | Likes coffee |
| `4377796229cb4682` | `id-2` | `identity` | Born in 1990 |
| `36a27eff4d8ac13a` | `persist-identity` | `identity` | User is left-handed |
| `ed330dce47b54d52` | `multi-identity` | `identity` | User prefers vim |
| `ed330dce47b54d52` | `multi-long-term` | `long-term` | Works in fintech |
| `ed330dce47b54d52` | `multi-short-term` | `short-term` | Debugging auth module |
| `ed330dce47b54d52` | `multi-ephemeral` | `ephemeral` | Current file is main.py |

Ten carry an out-of-enum tier. The other two carry `ephemeral`, which *is* a
`MemoryTier` member — so the tier-based probes never see them — but they are
from the same write event and are the same contamination.

These four `project_id`s hold **no other rows**: 12 rows in
`private_memories`, 0 in `feedback_events`, 12 in `audit_log` (the `save`
records for these same rows). There is no real tenant behind any of them.

## Where they came from

They are the literal keys and values of
`tests/integration/test_profile_integration.py` — compare
`TestExplicitProfileCustomTiers` (`:69-80`) and the persistence test (`:369`)
against the table above. That file constructs a real `MemoryStore` bound to the
built-in `personal-assistant` profile, whose layer names are exactly
`identity` / `long-term` / `short-term`. The four `project_id`s are
`derive_project_id(tmp_path)` hashes from four different test functions.

The route in was `tests/conftest.py`'s autouse in-memory-backend fixture, which
deliberately steps aside when `TAPPS_BRAIN_DATABASE_URL` is set
(`_inject_in_memory_private_backend`, condition at `tests/conftest.py:453` on
the pre-fix tree). Run with the deployed brain's DSN in the environment — which
`.envrc` → `.env` provides via direnv — the integration suite writes to
production. Nothing rejected the writes because writing a profile layer tier is
*legal*: `MemoryEntry._normalize_tier` (`src/tapps_brain/models.py:422-450`)
passes unrecognised strings through as possible EPIC-010 layer names, and
`normalize_save_tier` resolved them against the store's in-process profile.

That profile was never persisted. All **102** rows in live `project_profiles`
are `repo-brain`, whose only layers are `architectural` / `pattern` /
`procedural` / `context`. So the tiers arrived with nothing in the database able
to price them — which is what made `decay._get_half_life` raise and what the
SLO-1 inner join hid.

The ingress is closed in this branch: `tests/_live_dsn_guard.py` +
`pytest_configure` in `tests/conftest.py` abort the session when a test DSN
names a deployed-brain database, and `tests/_pg_fixture.py` now enforces the
promise its docstring already made.

## Proposed remediation

**Recommendation: delete all 12 rows** (not just the 10 with bad tiers).

Rationale:

1. They are **not tenant data**. Four synthetic `project_id`s with no feedback
   events, no other memories, and no consumer. Nothing recalls them.
2. Their content is fabricated test fixture text ("User name is Alice", "Likes
   coffee"). Retaining it has no value and it reads as real user data in any
   dump or export.
3. Retiering them (`identity` → `architectural`, etc.) would *preserve* fake
   personal facts in a production store and leave four ghost tenants in the
   tenant census. Closing validity (`status='stale'`) has the same problem and
   additionally strands them: GC never auto-archives `status='stale'` rows.
4. Deleting only the 10 bad-tier rows would leave the two `ephemeral` rows from
   the same event behind — an arbitrary split along a line that has nothing to
   do with why the rows are wrong.

### Dry-run / diff

```sql
-- Expected: exactly 12 rows, all four project_ids, all created 2026-08-07.
SELECT project_id, agent_id, key, tier, created_at
FROM private_memories
WHERE project_id IN ('36a27eff4d8ac13a','4377796229cb4682',
                     '56281493d732ffce','ed330dce47b54d52')
ORDER BY project_id, created_at;

-- Guard: must return 0. If it does not, a real tenant has collided with one of
-- these ids and the whole proposal is void.
SELECT count(*) FROM private_memories
WHERE project_id IN ('36a27eff4d8ac13a','4377796229cb4682',
                     '56281493d732ffce','ed330dce47b54d52')
  AND created_at::date <> DATE '2026-08-07';
```

### Backup table

```sql
CREATE TABLE tap6698_tier_quarantine_20260828 AS
SELECT * FROM private_memories
WHERE project_id IN ('36a27eff4d8ac13a','4377796229cb4682',
                     '56281493d732ffce','ed330dce47b54d52');
-- Expect: 12 rows.
```

### Apply (only after operator ACCEPT)

```sql
BEGIN;
DELETE FROM private_memories
WHERE project_id IN ('36a27eff4d8ac13a','4377796229cb4682',
                     '56281493d732ffce','ed330dce47b54d52');
-- Expect: DELETE 12
COMMIT;
```

The 12 `audit_log` rows for these scopes are left in place — the audit trail of
how bad data arrived is the part worth keeping.

### Verification after apply

```sql
SELECT tier, count(*) FROM private_memories
WHERE tier NOT IN ('architectural','pattern','procedural','context',
                   'ephemeral','session')
GROUP BY tier;
-- Expect: 0 rows.
```

and `/healthz?deep=1` → `retention_detail.no_overdue_active_rows` should report
`unrecognised_tier_total: 0`.

## Explicitly out of scope for this proposal

`retention_slo.check_no_overdue_active_rows` will, once deployed, also surface
genuinely **overdue** rows. Measured read-only against live on 2026-08-28, the
true total is **5,618** (5,608 overdue by age + the 10 unrecognised-tier rows
above); the pre-fix check reported **20**, the sample cap. `retention_ok` on
`/healthz?deep=1` therefore flips to `false` once this branch deploys — it was
already breached, the probe just could not say so. Deciding what to do about
those 5,608 rows is a separate call; this proposal covers only the twelve
contamination rows.

For the same reason, SLO 4 will report `violating_total: 100` with
`missing_cursor_total: 99` on the first deploy, and should fall to near zero
after one apply-mode maintenance cycle runs the new cross-tenant
`flywheel_all_tenants` pass.
