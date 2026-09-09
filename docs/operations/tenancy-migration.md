# Tenancy Migration Runbook — tapps-brain

**Covers TAP-7279 (decision on TAP-7260): re-homing the S3 population.**

## What this is

`python -m tapps_brain.maintenance.tenancy_migrate` re-homes rows that arrived
under a throwaway or misattributed tenant identity — the **S3 population**:

```sql
private_memories  WHERE project_id IN ('default','api','main','repo-brain')
                      OR agent_id IN ('default','unknown')
private_relations WHERE <the same predicate on its tenant columns>
```

Every row is classified by provenance (first rule to fire wins) and either
**re-homed** to its correct `(project_id, agent_id)` or **archived**. See the
rule table below and the module docstring
(`src/tapps_brain/maintenance/tenancy_migrate.py`) for the full rationale,
including two schema-forced deviations: `private_relations` has no
`key`/`source_agent` columns (so only R4/R5 ever fire for it) and no
`updated_at` column (so its collisions break ties on `created_at` instead).

The tool never touches `audit_log`, `experience_events`, `feedback_events`,
`kg_*`, `gc_archive`, or `hive_*` — it only **counts** their S3-predicate rows
in the plan's `deferred_tables` section (scope ruling S3; a successor story
owns them).

## Rule table

| Rule | Fires when | Outcome |
|------|-----------|---------|
| R1 | Earliest `save`/`remember` `audit_log` event for the row's `key` names a registered, **approved** project | Re-home to that project; `agent_id` unchanged |
| R2 | `source_agent` maps to a registered approved project via a caller-supplied agent→project map (absent map → rule skipped) | Re-home to that project; `agent_id` unchanged |
| R3 | `mem-<slug>-<hash>` key, `source_agent='unknown'`, project is `nlt-ideas-scout`, `created_at` inside a caller-supplied window (absent window → rule skipped) | Keep project; `agent_id → 'ingest'` |
| R4 | Row is under a REAL project (not one of the four S3 placeholders) with `agent_id` in `('default','unknown')` and no rule above fired | Keep project; `agent_id → 'legacy-unattributed'` |
| R5 | Everything else | Archive |

**R1/R2 change `project_id` only.** A row can still carry `agent_id` in
`('default','unknown')` after an R1/R2 re-home and would still match the S3
predicate's `agent_id` clause. This is intentional and safe — the tool
addresses every read/write by the row identity captured *before* any
mutation, never by re-evaluating the predicate mid-apply (see the module
docstring's "Row identity, not predicate re-evaluation" note). A second run
against the same database will pick such a row up again and this time land it
on R4, which is expected, idempotent behavior, not a bug.

## Collision policy

`agent_id` is part of the primary key `(project_id, agent_id, key)`. A
re-home that changes either column can collide with a row that already lives
at the target identity. The dry run enumerates every predicted collision
(`plan.tables[<table>].collisions`); apply resolves each one by keeping the
newer row (`updated_at` for `private_memories`, `created_at` for
`private_relations` — it has no `updated_at`) and archiving the loser. Ties go
to the source (S3) row. Every collision is counted separately in the apply
result (`collisions`), not folded into `re_homed_count`/`archived_count`.

## Dry run

```bash
python -m tapps_brain.maintenance.tenancy_migrate \
    --dsn "$TAPPS_BRAIN_MIGRATOR_DSN" \
    --dry-run \
    --plan-out /tmp/tenancy-plan.json \
    --agent-project-map /tmp/agentforge-agent-project-map.json \
    --ingest-window-start 2026-06-01T00:00:00+00:00 \
    --ingest-window-end 2026-06-30T23:59:59+00:00
```

- `--dsn` must authenticate as a role with `BYPASSRLS` (or the table owner
  connecting without `FORCE ROW LEVEL SECURITY` applied to it) — see
  [DB Roles Runbook](./db-roles-runbook.md). `private_memories` has FORCE RLS
  with **no** admin-bypass policy
  (`src/tapps_brain/migrations/private/012_rls_force.sql`), so the ordinary
  `tapps_runtime` application role cannot see the whole S3 population across
  tenants. Never point this at `tapps_runtime`'s DSN.
- `--agent-project-map` is optional (JSON `{source_agent: project_id}`, from
  AgentForge's `projects`/agents tables) — omitting it means R2 is
  structurally skipped for every row, recorded in the plan's
  `rule_config.r2_skipped`.
- `--ingest-window-start`/`--ingest-window-end` are optional and must be given
  together — omitting them skips R3 (`rule_config.r3_skipped`).
- The dry run issues only `SELECT`/`WITH` statements — it is provably
  read-only (`tests/test_tenancy_migration.py::TestDryRunIsReadOnly`).
- **Refuses with exit code 2** when the S3 predicate matches zero rows across
  both tables — a migration that finds nothing is a wrong `--dsn` or a wrong
  predicate, never treated as a clean result.

**Review the plan before applying.** Read `plan.tables.private_memories.groups`
and `plan.tables.private_relations.groups` — each names the exact rule and
target for every row (`row_ids`) — and `plan.tables.*.collisions` for every
predicted primary-key collision and its predicted winner.

## Apply

```bash
python -m tapps_brain.maintenance.tenancy_migrate \
    --dsn "$TAPPS_BRAIN_MIGRATOR_DSN" \
    --apply \
    --plan /tmp/tenancy-plan.json \
    --archive-table tenancy_migration_2026_09_09
```

- `--archive-table` names the safety-net table for `private_memories`
  (`private_relations`'s archive table is the same name with a `_relations`
  suffix). The name must match `^[a-z][a-z0-9_]{0,62}$` — anything else is
  refused before any SQL runs.
- Apply re-checks the S3 predicate's live `SELECT count(*)` against the plan's
  recorded count for each table and **refuses** (raises `StalePlanError`,
  no write issued) if they disagree — re-run `--dry-run` and review the new
  plan.
- On success, apply verifies `remaining_after == 0` (every original S3 row
  identity is gone from the live table — moved or archived) and that the
  archive-table snapshot count matches the pre-apply total, rolling back on
  any mismatch (`ApplyIntegrityError`).
- Every re-homed row's `tags` gains `migrated_from:<old project>/<old agent>`.

## Rollback

Apply runs in one transaction per invocation — a failure (stale plan,
integrity check) rolls back automatically and leaves the database untouched.
To undo a **successful** apply, restore from the archive tables it created:

```sql
-- Inside a transaction, after confirming which rows to restore:
INSERT INTO private_memories
SELECT * FROM tenancy_migration_2026_09_09
ON CONFLICT (project_id, agent_id, key) DO NOTHING;

INSERT INTO private_relations
SELECT * FROM tenancy_migration_2026_09_09_relations
ON CONFLICT (project_id, agent_id, subject, predicate, object_entity) DO NOTHING;
```

Restoring a re-homed row this way does **not** undo the row's new identity or
its `migrated_from` tag — it only restores the pre-apply snapshot's copy at
its *original* identity, which will re-collide with the row's new home if both
are live. Review `plan.tables.*.groups` first to know which identity each
restored row should end up at, and delete or re-migrate accordingly.

## Testing

`tests/test_tenancy_migration.py` covers the rule functions (pure, no DB) and
the dry-run/apply flows against a disposable Postgres fixture
(`tests/_pg_fixture.py` — never the deployed brain):

```bash
uv run pytest tests/test_tenancy_migration.py -v
```

## Related docs

- [DB Roles Runbook](./db-roles-runbook.md) — which role to authenticate
  `--dsn` as, and why `tapps_runtime` cannot be used here.
- `src/tapps_brain/maintenance/tenancy_migrate.py` — the module docstring has
  the full rule rationale and the identity-vs-predicate design note.
