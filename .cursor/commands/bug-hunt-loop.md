---
description: >-
  Multi-hour autonomous bug-hunt loop for tapps-brain. Each hour: find up to N
  real functional bugs across backend, visual, database, deployment, and the
  running local hive stack; fix them; independent sub-agent review per fix.
  Low-CPU verification only — no full regression runs.
argument-hint: "[hours: default 8] [bugs-per-hour: default 10]"
---

# Bug Hunt Loop

Run an hourly bug-hunt loop against **tapps-brain only** for **H hours**
(default **8**). Each cycle finds and fixes up to **B real bugs** (default **10**).

## Parse arguments

Read optional integers from the user message after the command (Cursor does
**not** expand `$1` / `$2`):

- First integer → `H` (hours). Default `8`. Clamp to `1..24`.
- Second integer → `B` (bugs per hour). Default `10`. Clamp to `1..20`.
- Examples: `/bug-hunt-loop` → `H=8 B=10`; `/bug-hunt-loop 4 5` → `H=4 B=5`.

Record `H`, `B`, and wall-clock start (`date -u +%Y-%m-%dT%H:%M:%SZ`) in the log.

## Hard rules (apply to every cycle)

- **Real bugs only.** Observable incorrect behavior: wrong results, crashes,
  data corruption, broken flows, dead endpoints, schema/code drift,
  log-visible errors, race conditions, broken deploy paths, UI rendering
  failures. NOT: style nits, refactors, TODOs, hypothetical edge cases,
  speculative hardening, “might be nicer if”.
- **No security/hardening focus.** Skip auth tightening, sanitization audits,
  CVE chasing. If you trip over a security check that is *functionally broken*,
  fix it — do not go hunting for them.
- **Scope: this repo + local hive stack only.** Never write to other repos,
  other Linear projects, or non-tapps-brain containers. AgentForge is
  **read-only evidence** (logs). Never modify AgentForge.
- **Target stack = local hive, not dev, not prod.**
  - **In scope:** compose project `tapps-brain` → containers `tapps-brain-http`,
    `tapps-brain-db`, `tapps-visual`, `tapps-brain-migrate`.
  - **Out of scope:** `tapps-brain-dev*` (pytest/`make brain-up`),
    `tapps-brain-prod-*`, any other host’s stack.
- **Low CPU.** Never run the full suite or coverage gate. Allowed:
  - `uv run pytest <one_file> -x -q` (or a single node id)
  - tests ranked by `tapps_diff_impact`
  - `make brain-smoke-live` (~10s)
  - `make brain-visual-smoke-live` (frontend/proxy)
  - `make brain-diagnostics-live` (DB/health)
  - ruff/mypy on **changed files only**
- **Every fix gets an independent code review** before it counts (step 4).
- **No wheel hot-install into the running container.** Code that must hit the
  live stack goes through `make dev-deploy` (or `MIGRATE=1 make dev-deploy`).
  Hot-copying a wheel skips image bake and leaves the next recreate stale.
- **Dedicated branch.** Before the first fix commit:
  `git checkout -b bug-hunt/YYYY-MM-DD` (UTC date). Never commit bug-hunt
  fixes onto an unrelated WIP feature branch.

## Setup (once, before hour 1)

1. `tapps_session_start()`.
2. Create/overwrite `.tapps-mcp/bug-hunt-log.md` with:
   - start time, `H`, `B`, branch name
   - table: `hour | bug | component | root cause | fix commit | review verdict | verification`
3. Snapshot the **hive** stack (exact names — a broad `name=tapps-brain` filter
   also matches `tapps-brain-dev-db` / `tapps-brain-prod-*`; Docker `name=` is
   substring match, not regex):

   ```bash
   docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' \
     | awk -F'\t' '$1=="tapps-brain-http" || $1=="tapps-brain-db" || $1=="tapps-visual"'
   docker ps --format '{{.Names}}\t{{.Status}}' \
     | awk -F'\t' '$1=="agentforge-api" || $1=="agentforge-main"'   # evidence only
   ```

4. Baseline: `make brain-smoke-live`. **Abort the loop** if smoke fails — fix
   stack health first; do not hunt on a broken baseline.
5. Create the bug-hunt branch if not already on one.

## Hourly cycle (hours 1..H)

### On wake (hours 2..H)

When an `AGENT_LOOP_TICK_bug_hunt` notification arrives you **MUST** run the
full hourly cycle below. Do **not** merely acknowledge the tick and idle.
If the chat was compacted, re-read `.tapps-mcp/bug-hunt-log.md` and continue
from the next incomplete hour.

### 1. Hunt (rotate focus — cover all components across H hours)

Confirm ≤ `B` leads per hour. Discard anything you cannot reproduce or
evidence with a log/query/curl.

**Suggested rotation** (wrap if `H < 8`; revisit high-signal areas if `H > 8`):

| Hour mod 8 | Primary focus |
|------------|---------------|
| 1 | Live logs + smoke regressions |
| 2 | HTTP adapter / REST (`/v1/*`, `/healthz`, `/snapshot`) |
| 3 | MCP tool/resource surface |
| 4 | Postgres schema + data integrity |
| 5 | Visual dashboard + proxy |
| 6 | Retrieval / decay / consolidation math |
| 7 | Deploy paths (Makefile, compose, scripts) |
| 0 | Cross-cutting: AgentForge consumer errors → brain-side root cause |

**Evidence sources:**

- **Logs (every hour):**
  ```bash
  docker logs tapps-brain-http --since 1h 2>&1 | grep -iE 'error|traceback|warn' | tail -50
  docker logs tapps-visual --since 1h 2>&1 | tail -30
  docker logs tapps-brain-db --since 1h 2>&1 | grep -iE 'error|fatal|deadlock' | tail -30
  docker logs agentforge-api --since 1h 2>&1 | grep -iE 'brain|memory|mcp' | grep -iE 'error|fail' | tail -30
  ```
- **Database (hive only — never `make brain-psql`):**
  `make brain-psql` talks to compose project **`tapps-brain-dev`** /
  DB `tapps_brain_dev`. For the live stack use:
  ```bash
  docker exec tapps-brain-db psql -U tapps -d tapps_brain -c '...'
  ```
  Look for: migration version vs `src/tapps_brain/migrations/`, orphaned rows,
  constraint failures, leaked `smoke-`/`test-` tenants (`make purge-test-tenants`
  dry-run is fine; only `APPLY=1` when intentional cleanup is the fix).
- **Live diagnostics:** `make brain-diagnostics-live`, `/healthz?deep=1`,
  scorecard / circuit-breaker anomalies.
- **Backend** (`src/tapps_brain/`): `tapps_call_graph` / `tapps_dependency_graph`
  for real call flows; logic errors, swallowed failures, protocol mismatches.
- **Visual:** `src/tapps_brain/visual_snapshot.py`, `docker/Dockerfile.visual`,
  `docker/nginx-visual.conf` — broken panels, stale snapshot, proxy mismatch
  (`make brain-visual-smoke-live`).
- **Deploy:** `docker/docker-compose.hive.yaml`, Makefile targets, `scripts/` —
  env contract vs `docs/guides/postgres-dsn.md` (esp. Dev vs deploy Postgres).
- **MCP:** drift vs `docs/generated/mcp-tools-manifest.json`; live round-trip
  via `make brain-healthcheck` (wiring check — not a substitute for
  `brain-smoke-live` after deploy).

### 2. Fix (one bug at a time)

- Root cause, smallest change.
- One targeted test that fails before / passes after (skip only for pure
  infra where smoke is the test). Prefer `uv run pytest path::node -x -q`.
- `tapps_quick_check(file_path)` after each Python edit.
- Commit each fix separately:
  ```text
  fix(bug-hunt): <short description>

  Evidence: <log line / query / repro>
  ```

### 3. Deploy + e2e verify (when the bug affects the running stack)

- `make dev-deploy`
  (`MIGRATE=1 make dev-deploy` if anything under `src/tapps_brain/migrations/`
  changed).
- Cheap gates only: always `make brain-smoke-live`; add
  `make brain-visual-smoke-live` for visual/proxy fixes;
  `make brain-diagnostics-live` for DB/health fixes.
- Re-check the original evidence source — symptom must be gone.

### 4. Independent code review (REQUIRED per fix)

Spawn a **separate** sub-agent (`Task` with `subagent_type="tapps-reviewer"`,
or Cursor agent `tapps-reviewer`) with an explicit correctness brief — do not
rely on the agent’s default “score the file” persona alone:

- diff of the fix
- bug evidence + claimed root cause
- verify: fix is correct, no scope creep, no missed callers
  (`tapps_impact_analysis` / `tapps_call_graph`), test covers the bug

Reject → fix → re-review before counting. Record verdict in the log.

### 5. Close the hour

- `tapps_validate_changed(file_paths="<explicit changed .py files>")` (quick).
- Append this hour’s rows to `.tapps-mcp/bug-hunt-log.md`. Note what was
  searched even if fewer than `B` bugs were confirmed — **do not invent bugs**.
- **Arm the next hour wake** (unless this was hour `H`). End-of-turn idle is
  not enough — previous loops lost hours 2–8 because ticks were never acted on.
  Use a one-shot monitored sleeper (see Cursor `/loop` skill):

  ```bash
  # block_until_ms: 0, notify_on_output pattern: ^AGENT_LOOP_TICK_bug_hunt
  sleep 3600
  echo 'AGENT_LOOP_TICK_bug_hunt {"hour":<N+1>,"of":<H>,"prompt":"Continue /bug-hunt-loop: run hour <N+1> fully"}'
  ```

  Confirm the sleeper started, then end the turn. On the next tick, execute
  hour `N+1` immediately.

## End of loop (after hour H)

1. `tapps_checklist(task_type="bugfix")` — resolve gaps.
2. Final `make brain-smoke-live`.
3. Summarize in `.tapps-mcp/bug-hunt-log.md`: totals found/fixed/reviewed,
   per-component breakdown, deferred confirmed bugs (file via `linear-issue`
   skill — tapps-brain Linear project only).
4. Kill any remaining bug-hunt sleeper PID.
5. `/tapps-handoff-session` — include branch name, whether `make dev-deploy`
   baked the image, and ship/PR next step.
