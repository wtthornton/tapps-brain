# Hive orphan triage (TAP-6816, b4 report half)

**Status: fixture-only.** No `TAPPS_BRAIN_DATABASE_URL` / `TAPPS_BRAIN_HIVE_DSN` was
available in the environment this report was generated in, so `scripts/triage_hive_orphans.py`
was never run against a live database — only against the unit-test fixture in
`tests/unit/test_triage_hive_orphans.py`. The numbers below are illustrative of the
script's output shape, not a real count. **Do not cite them as the current orphan
count** — re-run the script against the live brain database through a read-only
connection to get that number.

## How to run it for real

```bash
uv run python scripts/triage_hive_orphans.py \
  --hive-dsn "$TAPPS_BRAIN_HIVE_DSN" \
  --private-dsn "$TAPPS_BRAIN_DATABASE_URL" \
  --output docs/operations/hive-orphan-triage-tap-6816-live.md
```

The script only issues `SELECT` statements (see its module docstring and
`tests/unit/test_triage_hive_orphans.py::test_every_statement_issued_is_select`) and
opens both connections with `default_transaction_read_only=on` set at the connection
level, so it cannot write or reap anything. **This lane produces the report only —
the reap/retain/rationale decision on TAP-6816 acceptance box b4 is made separately
by the operator.**

## Orphan definition used

A `hive_memories` row is an orphan when no `private_memories` row matches on
`(agent_id, key) == (hive_memories.source_agent, hive_memories.key)` with
`status = 'active'`. Derived from `PropagationEngine.propagate` in
`src/tapps_brain/backends.py:408`, which writes `hive_store.save(key=key,
source_agent=agent_id, ...)` — the hive row's `key` is the private row's `key`
verbatim, and `source_agent` is the private row's `agent_id`.
`hive_memories.namespace` is derived from `agent_scope`/profile
(`backends.py:96-131`), **not** `project_id` — there is no `project_id` column on
`hive_memories` at all, which is why project attribution for an orphan comes from
whichever of `private_memories` (non-active status match) or `gc_archive`
(`src/tapps_brain/migrations/private/006_gc_archive.sql`) has a row for that
`(agent_id, key)`, and is `"unknown"` when neither does.

- **archived**: a `private_memories` row matches `(agent_id, key)` with a
  non-`'active'` status (`stale`/`superseded`/`archived`/`contradicted`, per
  `migrations/private/027_memory_status.sql` and `033_status_contradicted.sql`),
  OR a `gc_archive` row matches.
- **absent**: neither table has a matching row.

## Example output (fixture data, NOT live counts)

```
# Hive orphan triage report (TAP-6816, b4 report half)

Hive rows counted (total population): 4
Orphans found: 3

## By project

- proj-c: 1
- proj-d: 1
- unknown: 1

## By age

- 7-30d: 1
- 30-90d: 1
- 365d+: 1

## Private counterpart

- absent: 1
- archived: 2
```
