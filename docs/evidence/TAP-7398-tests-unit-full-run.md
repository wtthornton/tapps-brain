# TAP-7398 — tapps-brain full `tests/unit` evidence run

Lane: L12 (evidence-only, no product code changed). Worktree: `/tmp/tmcp-burndown-l12`.

## Measured starting state

- Worktree HEAD: `bc6789aef0a5cbbefcc7cb1b877ca7f077a7842f` (detached, matches the
  merged sha named in the lane brief — no later commits landed on `main` before
  this run).
- `src/tapps_brain/migrations/roles/001_db_roles.sql` exists and was applied
  after schema migrations, per the documented order (private → hive →
  federation → roles).
- `scripts/apply_all_migrations.py` exists and is the migration entrypoint.

## Recipe actually executed

1. **Standalone pgvector container, non-default port, plain `docker run` (no
   compose):**

   ```
   docker run -d --name tmcp-l12-pgvector \
     -e POSTGRES_USER=tapps -e POSTGRES_PASSWORD=tapps_test_pw \
     -e POSTGRES_DB=tapps_brain_test \
     -p 15433:5432 pgvector/pgvector:pg17
   ```

   Port **15433** (host) — verified free before starting (`docker ps -a` showed
   no container already bound to it; the live stack's `tapps-brain-db` has no
   host port published at all, and other local pgvector containers on this
   host use 5432/5433/15432/328xx). No `compose`, no `--remove-orphans`, the
   live `tapps-brain-http` / `tapps-brain-db` containers were never touched.

2. **`uv sync --group dev --python 3.12`** — resolved `tapps-brain==3.32.2
   (from file:///tmp/tmcp-burndown-l12)`, i.e. installed editable from this
   lane's own worktree, not the primary checkout.

3. **`CREATE EXTENSION vector`** on the fresh database, then
   **`scripts/apply_all_migrations.py`** against
   `postgresql://tapps:tapps_test_pw@localhost:15433/tapps_brain_test` — applied
   private v1–33, hive v1–5, federation v1–2, `All migrations applied
   successfully.` (full log captured in terminal history for this run).

4. **Provisioned `tapps_runtime`/`tapps_migrator`/`tapps_readonly`** by running
   `src/tapps_brain/migrations/roles/001_db_roles.sql` against the same DSN as
   the database superuser — matches the CI-documented order (`.github/workflows/ci.yml`,
   "Provision runtime roles" step).

5. Ran the suite with the **exact CI recipe** from `.github/workflows/ci.yml`'s
   `test` job:

   ```
   TAPPS_BRAIN_DATABASE_URL=postgresql://tapps:tapps_test_pw@localhost:15433/tapps_brain_test
   TAPPS_TEST_POSTGRES_DSN=postgresql://tapps:tapps_test_pw@localhost:15433/tapps_brain_test
   TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1
   HF_DATASETS_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1
   uv run --project /tmp/tmcp-burndown-l12 pytest tests/unit/ -v --tb=short \
     -m "not benchmark" -p print_brain_file_plugin
   ```

   `print_brain_file_plugin` is a one-purpose pytest plugin added at
   `scripts/print_brain_file_plugin.py` (`pytest_configure` hook) whose sole job
   is printing `tapps_brain.__file__` from inside the same test process that
   ran the suite — not a separate command run afterward.

## Detour worth recording (not evidence, diagnosis only)

A first attempt connected as the DB superuser **without**
`TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1` set. `PostgresConnectionManager`'s
startup guard (`postgres_connection.py:404`, added for TAP-512) correctly
refused every `MemoryStore(...)` construction with `RuntimeError: tapps-brain
refuses to start as a privileged Postgres role`, producing 742 errors and 532
failures — a self-inflicted misconfiguration, not a product defect. CI's own
comment in `.github/workflows/ci.yml` documents exactly this trade-off
("CI connects as the owner role because the tests include schema migrations;
`TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE` is the documented override for CI/dev").
Re-running with the flag set (step 5 above) reproduced CI's real recipe and is
the run counted as evidence below. That misconfigured run's log was not kept
as evidence (it demonstrates my own setup mistake, not a suite result) — the
exit code and error signature are recorded here for anyone re-deriving this.

## VAL-19 — full-suite proof

**Positive control (real pgvector, real migrations, real roles, CI recipe):**

```
========= 5575 passed, 95 skipped, 1501 warnings in 336.47s (0:05:36) ==========
```
Exit code: `0`.

`tapps_brain.__file__` printed from inside the same pytest process
(`pytest_configure` hook, same run, same stdout capture):

```
TAPPS_BRAIN_FILE=/tmp/tmcp-burndown-l12/src/tapps_brain/__init__.py
```

This resolves into **this lane's own worktree** (`/tmp/tmcp-burndown-l12`), not
the primary checkout at `/home/wtthornton/code/tapps-brain` — the editable
install was not shadowed.

Full verbatim output: [`TAP-7398-positive-control-pgvector.txt`](./TAP-7398-positive-control-pgvector.txt)
(5,670 tests collected — identical collected count to the negative control
below; no test was deselected, `-k`'d, or `--ignore`'d to reach this result).

**Negative control (plain local pytest, no DSN set at all — must be rejected
as a false green):**

```
========== 5558 passed, 112 skipped, 3 warnings in 204.54s (0:03:24) ===========
```
Exit code: `0`.

This is **not accepted as evidence of the full suite passing against a real
backend.** It exits 0 and *looks* green, but 112 tests silently skipped
(17 more than the positive run) because every DB-dependent fixture backs off
to a skip when no `TAPPS_BRAIN_DATABASE_URL`/`TAPPS_TEST_POSTGRES_DSN` is set
rather than erroring — the classic false-green shape this acceptance box warns
about. No pgvector container was involved in this run at all; the delta in
skip count (112 vs. 95) between the two runs is the empirical signature of the
tests that only run against a live backend.

Full verbatim output: [`TAP-7398-negative-control-no-dsn.txt`](./TAP-7398-negative-control-no-dsn.txt)
(same 5,670 collected count as the positive run — the two are apples-to-apples
on the same collected set, differing only in backend availability).

## Conclusion

The full `tests/unit` suite passes (5575 passed, 95 skipped, 0 failed) against
a real pgvector backend on `bc6789ae`, run from this lane's own worktree with
the import path proven inside the same process. No product code, test
assertion, or skip/xfail marker was changed to reach this result.
