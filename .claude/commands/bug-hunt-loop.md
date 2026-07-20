---
description: >-
  8-hour autonomous bug-hunt loop for tapps-brain. Each hour: find up to 10
  real functional bugs across all components (backend, frontend/visual,
  database, deployment, running Docker stack), fix them, and have a separate
  sub-agent code-review every fix before it counts. Low-CPU verification only —
  no full regression runs.
argument-hint: "[hours: default 8] [bugs-per-hour: default 10]"
---

# Bug Hunt Loop

Run an hourly bug-hunt loop against **tapps-brain only** for **8 hours**
(or `$1` hours if given). Each cycle finds and fixes up to **10 real bugs**
(or `$2` if given).

## Hard rules (apply to every cycle)

- **Real bugs only.** A "bug" is observable incorrect behavior: wrong results,
  crashes, data corruption, broken flows, dead endpoints, schema/code drift,
  log-visible errors, race conditions, broken deploy paths, UI rendering
  failures. NOT: style nits, refactors, TODOs, hypothetical edge cases,
  speculative hardening.
- **No security/hardening focus.** Skip auth tightening, input-sanitization
  audits, CVE chasing, etc. Focus on *functional*, meaningful fixes. (If you
  trip over an actual security *bug* — a broken check that fails functionally —
  fix it, but do not go hunting for them.)
- **Scope: this repo and this project's deployed stack only.** Never write to
  other repos, other Linear projects, or non-tapps-brain containers.
  AgentForge containers are read-only evidence sources (logs), never targets.
- **Low CPU.** Never run the full test suite or full coverage gate. Allowed:
  single test files (`pytest tests/unit/test_x.py -x -q`), targeted tests
  selected via `tapps_diff_impact`, `make brain-smoke-live` (~10s),
  `make brain-visual-smoke-live`, `make brain-diagnostics-live`, ruff/mypy on
  changed files only.
- **Every fix gets an independent code review** by a separate sub-agent before
  it counts as done (see step 4).

## Setup (once, before hour 1)

1. `tapps_session_start()`.
2. Record loop start time. Create a scratch log at
   `.tapps-mcp/bug-hunt-log.md` with a table: `hour | bug | component |
   root cause | fix commit | review verdict | verification`.
3. Snapshot the running stack:
   - `docker ps --filter name=tapps-brain --filter name=tapps-visual --filter name=agentforge`
   - `make brain-smoke-live` to confirm a healthy baseline before touching anything.

## Hourly cycle (repeat until 8 hours elapsed)

### 1. Hunt (rotate focus areas so all components get covered across the 8 hours)

Pick evidence-driven leads, up to 10 per hour, from sources like:

- **Running system logs** (highest-signal, check every hour):
  - `docker logs tapps-brain-http --since 1h 2>&1 | grep -iE "error|traceback|warn" | tail -50`
  - `docker logs tapps-visual --since 1h 2>&1 | tail -30`
  - `docker logs tapps-brain-db --since 1h 2>&1 | grep -iE "error|fatal|deadlock" | tail -30`
  - AgentForge as a *consumer* of the brain: `docker logs agentforge-api --since 1h 2>&1 | grep -iE "brain|memory|mcp" | grep -iE "error|fail" | tail -30` (evidence only — fix the brain side, never AgentForge).
- **Database data + schema**: `make brain-psql` (or `docker exec tapps-brain-db psql ...`) —
  orphaned rows, constraint violations waiting to happen, drift between
  `src/tapps_brain/migrations/` and the live schema, leaked `smoke-`/`test-`
  tenants, index bloat causing wrong/slow results.
- **Live diagnostics**: `make brain-diagnostics-live`, `/healthz?deep=1`,
  diagnostics scorecard anomalies, circuit-breaker state.
- **Backend code** (`src/tapps_brain/`): trace real call flows with
  `tapps_call_graph` / `tapps_dependency_graph`; look for logic errors,
  off-by-one decay/scoring math, broken error paths that swallow failures,
  dead branches that should be live, protocol/backend mismatches.
- **Frontend** (`tapps-visual` dashboard): broken panels, stale/incorrect
  snapshot rendering, proxy mismatches (`make brain-visual-smoke-live`).
- **Deployment**: `docker/docker-compose.hive.yaml`, `Makefile` targets,
  `scripts/` — broken or drifted commands, env-var contract violations
  vs `docs/guides/postgres-dsn.md`.
- **MCP surface**: tool/resource drift vs `docs/generated/mcp-tools-manifest.json`,
  broken tool responses via a live `/mcp/` round-trip (`make brain-healthcheck`).

For each lead, **confirm it is real** (reproduce it or capture the log/query
evidence) before it counts toward the 10. Discard anything you cannot confirm.

### 2. Fix (one bug at a time)

- Root cause, not symptom. Smallest change that fixes the confirmed behavior.
- Write or adapt **one targeted test** that reproduces the bug and passes after
  the fix (skip only for pure infra/deploy fixes where a smoke check is the test).
- `tapps_quick_check(file_path)` after each Python edit.
- Commit each fix separately: `fix(bug-hunt): <short description>` with the
  evidence (log line / query / repro) in the commit body.

### 3. Deploy + e2e verify (when the bug lives in the running stack)

- Code fix affecting the deployed brain: `make dev-deploy`
  (`MIGRATE=1 make dev-deploy` if SQL under `src/tapps_brain/migrations/` changed).
- Verify with the cheap e2e gates only: `make brain-smoke-live`, plus
  `make brain-visual-smoke-live` for frontend fixes and
  `make brain-diagnostics-live` for DB/health fixes.
- Re-check the original evidence source (the log/query that exposed the bug)
  to confirm the symptom is gone.

### 4. Independent code review (REQUIRED per fix)

After each fix is complete, spawn a **separate sub-agent** (`tapps-reviewer`;
one review task per fix or per small batch of related fixes) with:

- the diff of the fix,
- the bug evidence and root-cause claim,
- instruction to verify: correctness of the fix, no scope creep, no missed
  callers (`tapps_impact_analysis` / `tapps_call_graph`), test actually
  covers the bug.

If the reviewer rejects, address the findings and re-review before counting
the bug as fixed. Record the verdict in `.tapps-mcp/bug-hunt-log.md`.

### 5. Close the hour

- `tapps_validate_changed(file_paths="<explicit changed .py files>")` (quick mode).
- Update `.tapps-mcp/bug-hunt-log.md` with the hour's row(s).
- If fewer than 10 confirmed-real bugs were found, that's fine — do NOT invent
  issues to hit the quota. Note what was searched and move on.
- Sleep/idle until the next hour boundary (do not burn CPU polling).

## End of loop (after final hour)

1. `tapps_checklist(task_type="bugfix")` and resolve any gaps.
2. Final `make brain-smoke-live` on the deployed stack.
3. Summarize in `.tapps-mcp/bug-hunt-log.md`: total bugs found/fixed/reviewed,
   per-component breakdown, anything confirmed-real but deferred (file those
   via the `linear-issue` skill).
4. `/tapps-handoff-session` to write the session handoff.
