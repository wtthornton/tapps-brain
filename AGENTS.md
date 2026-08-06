<!-- tapps-agents-version: 3.12.69 -->
# TappsMCP - instructions for AI assistants

When the **TappsMCP** MCP server is configured, you have access to tools for **code quality, doc lookup, and domain expert advice**. Use them to avoid hallucinated APIs, missed quality steps, and inconsistent output.

**File paths:** Use paths relative to project root (e.g. `src/main.py`). Absolute host paths also work when `TAPPS_MCP_HOST_PROJECT_ROOT` is set.

---

## Quick start: clone → compose → pytest (≤ 15 min)

```bash
# 1. Clone and install
git clone https://github.com/your-org/tapps-brain
cd tapps-brain
uv sync --group dev           # creates .venv; Python 3.12+ required

# 2. Start Postgres + pgvector (Docker required)
make brain-up                 # pulls pgvector/pgvector:pg17, waits for ready

# 3. Apply schema migrations (private, hive, federation)
make brain-migrate            # idempotent — safe to re-run

# 4. Run the full test suite
make brain-test               # pytest with coverage gate ≥ 95 %

# 5. Tear down when done
make brain-down               # removes containers + volumes
```

Expected total time: ~5–12 min depending on image pull and hardware.

### All Makefile targets

| Target | Description |
|---|---|
| `make brain-up` | Start **dev** Postgres+pgvector (`tapps-brain-dev` compose project; safe alongside `hive-up`) |
| `make brain-down` | Stop dev containers and remove volumes |
| `make brain-restart` | Restart the Postgres container (keeps data) |
| `make brain-psql` | Open a psql shell in the running container |
| `make brain-migrate` | Apply all pending schema migrations (idempotent) |
| `make brain-test` | Full test suite with coverage (≥ 95 %) |
| `make brain-test-fast` | Tests excluding benchmarks, no coverage, fail-fast (`-x`), parallel (`-n auto`) |
| `make brain-lint` | Ruff lint + format check |
| `make brain-type` | Strict mypy type check |
| `make brain-qa` | Full QA: lint + type + tests (mirrors CI) |
| `make brain-healthcheck` | Live MCP initialize + `brain_recall` (server-mode OK when `.mcp.json` is bridge-only) |
| `make brain-smoke-live` | **Canonical post-deploy gate** — HTTP smoke (`/healthz`, `experience:query` round-trip) |
| `make brain-diagnostics-live` | Live stack diagnostics (`/healthz?deep=1`, snapshot, stale, scorecard). Uses `BRAIN_LIVE_*` env (not host `TAPPS_BRAIN_*`). `AUTO_GC=1` archives stale candidates. |
| `make brain-visual-smoke-live` | Visual dashboard smoke (`:8088/` meta, proxied + direct `/snapshot` schema) |
| `make dev-deploy` | Fast Docker loop: reload brain + live smoke ([dev-docker-loop.md](docs/guides/dev-docker-loop.md)) |
| `make hive-reload-http` | Rebuild wheel + http image only; restart brain container |
| `make hive-reload` | Rebuild + run migrate sidecar when SQL changed; restart brain |
| `make hive-smoke` | Isolated compose smoke (alternate ports; boots and tears down) |
| `make publish-brain-image` | Build wheel + `docker-tapps-brain-http:latest` + versioned tag (for AgentForge) |

### Local Docker stack (agents)

When the user asks to **upgrade / redeploy tapps-brain to local Docker**, use the fast inner loop — not a full `hive-deploy` unless images/nginx/visual changed or the stack is new.

| Situation | Command |
|---|---|
| **First-time** stack | `cp docker/.env.example docker/.env` → fill secrets → `make hive-deploy` |
| **Code upgrade** (default, 10–20×/day) | `make dev-deploy` — wheel + http image rebuild + `brain-smoke-live` |
| **SQL migrations** changed | `MIGRATE=1 make dev-deploy` |
| **`docker/.env` only** (no rebuild) | `docker compose -p tapps-brain -f docker/docker-compose.hive.yaml up -d --no-deps --force-recreate tapps-brain-http` |
| **Verify** after deploy | **`make brain-smoke-live`** (~10s). Optional: `make brain-visual-smoke-live`. Use `make brain-healthcheck` for live MCP round-trip / consumer wiring — not as the stack upgrade gate. |
| **Version bump / visual / all images** | Align `BRAIN_VERSION` in `docker/.env` to `pyproject.toml`, then `make publish-brain-image` + compose `up -d` (see [dev-docker-loop.md](docs/guides/dev-docker-loop.md)) |

**Required in `docker/.env`:** `TAPPS_BRAIN_ALLOWED_ORIGINS` must be a non-empty comma-separated list. Compose sets `TAPPS_BRAIN_STRICT=1`; without origins the `tapps-brain-http` container crash-loops (`Connection reset by peer` on `:8080`). Local dev template value:

```bash
TAPPS_BRAIN_ALLOWED_ORIGINS=http://127.0.0.1:8088,http://localhost:8088
```

Keep DB + visual running between iterations — do **not** `make hive-down` between code deploys. Full workflow: [`docs/guides/dev-docker-loop.md`](docs/guides/dev-docker-loop.md).

### DSN override

The default dev DSN is `postgres://tapps:tapps@localhost:5432/tapps_brain_dev` (matches `make brain-up`, compose project `tapps-brain-dev`). The **deployed** hive DB uses project `tapps-brain` and hostname `tapps-brain-db` on `tapps-brain_default` — do not mix the two on the same Docker network (see `docs/guides/postgres-dsn.md` § Dev vs deploy Postgres).
Override with:

```bash
make brain-test TAPPS_DEV_DSN="postgres://me:pw@myhost:5432/tapps_brain"
```

See [`docs/guides/postgres-dsn.md`](docs/guides/postgres-dsn.md) for the **full env-var contract** (all variables, examples, required (prod/dev)). Template: [`.env.example`](.env.example); Docker deploy template: [`docker/.env.example`](docker/.env.example).

### Key environment variables

| Variable | Purpose |
|---|---|
| `TAPPS_BRAIN_DATABASE_URL` | Single Postgres DSN — private memory + (by default) Hive + Federation. In production, connect as the DML-only `tapps_runtime` role created by the migrate sidecar. |
| `TAPPS_BRAIN_HIVE_DSN` | **Optional advanced override.** Put Hive on a physically separate Postgres. Unset → inherits `TAPPS_BRAIN_DATABASE_URL`. |
| `TAPPS_BRAIN_FEDERATION_DSN` | **Optional advanced override.** Same rule for Federation. |
| `TAPPS_BRAIN_AUTO_MIGRATE` | Set `1` to auto-apply pending private-schema migrations at `MemoryStore` startup. Not recommended on the containerized brain (runs as `tapps_runtime`, no DDL). Use the migrate sidecar. |
| `TAPPS_BRAIN_AGENT_ID` | Agent identity string. |
| `TAPPS_BRAIN_PROJECT_DIR` | Project root path. |
| `TAPPS_BRAIN_GROUPS` | CSV group memberships (e.g. `dev-pipeline,frontend-guild`). |
| `TAPPS_BRAIN_EXPERT_DOMAINS` | CSV expert domains for auto-publish. |

### CI

GitHub Actions (`ci.yml`) runs the same `pytest` command against a
`pgvector/pgvector:pg17` service container (credentials: `tapps/tapps/tapps_brain_dev`)
on every push and PR — no Docker needed locally just for CI. The
`TAPPS_BRAIN_DATABASE_URL` and `TAPPS_TEST_POSTGRES_DSN` env vars are set
automatically in CI, and `scripts/apply_all_migrations.py` runs before pytest
to ensure all schema migrations are applied.

## Delivery queue

Run commands from the **repository root** (the directory that contains `pyproject.toml`).

**Canonical queue:** [tapps-brain Linear project](https://linear.app/tappscodingagents/project/tapps-brain-e5604347c7db). Epic specs: `docs/planning/epics/`. Ralph autonomous loop **retired 2026-06-09** — see `docs/planning/epics/EPIC-077.md`.

## v3 Load Smoke (concurrent-agent benchmark)

### benchmark-postgres (canonical — STORY-066.9)

Pytest-based load smoke: **50 concurrent agents × 60 s** against one Postgres, recording p95
latency for `save`, `recall`, and `hive_search`.  Results are **informational only** (pre-SLO).
Requires `TAPPS_BRAIN_DATABASE_URL` and a running Postgres with schemas applied (`make brain-migrate`).

```bash
# Quick start: Makefile target (sets DSN from .env if present)
make benchmark-postgres

# Or run directly:
TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5433/tapps_brain \
    pytest tests/benchmarks/load_smoke_postgres.py -v -s

# Shorter run for quick local validation (10 seconds instead of 60):
TAPPS_SMOKE_DURATION=10 \
TAPPS_BRAIN_DATABASE_URL=postgres://tapps:tapps@localhost:5433/tapps_brain \
    pytest tests/benchmarks/load_smoke_postgres.py -v -s
```

Override env vars:

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_SMOKE_AGENTS` | `50` | Number of concurrent agent threads |
| `TAPPS_SMOKE_DURATION` | `60` | Wall-clock seconds each agent runs |
| `TAPPS_BRAIN_DATABASE_URL` | *(required)* | Postgres DSN |

The test is marked `requires_postgres` and `benchmark` — it is excluded from the fast unit
suite (`-m "not benchmark"`) and auto-skipped when `TAPPS_BRAIN_DATABASE_URL` is unset.

## `requires_postgres` pytest marker

Integration tests that require a live PostgreSQL instance are marked with
`@pytest.mark.requires_postgres`. The `pytest_collection_modifyitems` hook in
`tests/conftest.py` auto-skips these tests when `TAPPS_BRAIN_DATABASE_URL` is unset.

```bash
# Run only Postgres integration tests (requires running Postgres)
export TAPPS_BRAIN_DATABASE_URL=postgresql://tapps:tapps@localhost:5433/tapps_brain
uv run pytest tests/integration/ -v -m requires_postgres

# Run unit tests only (no Postgres required)
uv run pytest tests/unit/ -v
```

### The unit suite runs against a *different backend* than CI (TAP-5633)

`tests/conftest.py` injects an in-process `InMemoryPrivateBackend` whenever a
`MemoryStore` is built with no explicit backend **and no DSN is set**. That keeps
the suite runnable without Postgres, but it means a bare `uv run pytest
tests/unit/` never exercises anything whose window is the unlocked write-through
persist — that persist returns in microseconds in-memory and takes a round-trip
against Postgres.

CI sets a DSN for the unit job, so **CI can be red on a test that is green
locally 12 runs in a row.** TAP-5633 was exactly this. To reproduce a CI-only
concurrency failure locally, match the CI environment:

```bash
TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1 \
TAPPS_BRAIN_DATABASE_URL="postgresql://tapps:tapps@localhost:55432/tapps_brain_dev" \
uv run pytest tests/unit/test_concurrent.py -q
```

Both variables are required. Without `TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1` the
run dies in `postgres_connection.py` on the privileged-role guard, which looks
like a test failure but is not one.

### Integration test files (STORY-066.13)

| File | Coverage |
|------|----------|
| `tests/integration/test_postgres_private_backend.py` | `PostgresPrivateBackend` save / load_all / delete / search CRUD |
| `tests/integration/test_feedback_postgres.py` | `FeedbackStore` record / query / strict-mode rejection |
| `tests/integration/test_session_index_postgres.py` | `SessionIndex` save_chunks / search / delete_expired |
| `tests/integration/test_agent_identity_postgres.py` | `(project_id, agent_id)` row isolation across multiple agents |
| `tests/integration/test_pgvector_embeddings.py` | pgvector embedding write + knn_search recall |

All tests generate unique `(project_id, agent_id)` pairs per test via `uuid.uuid4()` to
prevent row collisions during parallel test execution.

### Test/load tenant cleanup (TAP-4465)

Against a persistent or shared Postgres, rows written under unique
`(project_id, agent_id)` keys leak unless explicitly removed. Two mechanisms keep
the database clean:

1. **Session-end purge.** A session-scoped autouse fixture in
   `tests/integration/conftest.py` records every `project_id` a
   `PostgresPrivateBackend` is built with and deletes those rows — across all
   tables that carry a `project_id` column — when the test session ends. It also
   sweeps any rows left under a reserved prefix.
2. **Reserved test-tenant prefixes.** Test, load, and smoke harnesses MUST name
   their throwaway tenants with a reserved prefix — `smoke-` or `test-` (see
   `RESERVED_TEST_PROJECT_PREFIXES` in `src/tapps_brain/maintenance_purge.py`).
   `scripts/load_smoke.py` deletes its `smoke-<hex>` project on exit (including on
   partial failure), and `scripts/brain_smoke_live.sh` purges its smoke project
   after the run.

To mop up any leaked rows on a live DB, run the operational valve (dry-run by
default):

```bash
# Preview rows matching the reserved prefixes
make purge-test-tenants
# Actually delete them
APPLY=1 make purge-test-tenants
# Or the CLI directly (custom prefix, JSON output):
uv run tapps-brain maintenance purge-test-tenants --prefix smoke- --apply
```

Full parity doc and latency budget: `docs/engineering/v3-behavioral-parity.md`.

### load_smoke.py (ad-hoc / script runner)

Flexible N-agent × M-ops run (not time-bounded). Useful for quick exploratory tests.

```bash
# Requires a running Postgres with private-memory schema applied
export TAPPS_TEST_POSTGRES_DSN="postgres://tapps:tapps@localhost:5432/tapps_test"

# 10 agents × 50 ops each (default)
python scripts/load_smoke.py

# Custom: 20 agents × 100 ops
python scripts/load_smoke.py --agents 20 --ops 100

# Without Postgres (in-memory store only, no DSN required)
python scripts/load_smoke.py --no-postgres
```

Outputs a latency table (p50/p90/p95/p99/max for save, recall, and per-agent wall time).
## Essential tools (always-on workflow)

| Tool | When to use |
|------|--------------|
| **tapps_session_start** | **FIRST call in every session** - server info only |
| **tapps_quick_check** | **After editing any Python file** - quick score + gate + security |
| **tapps_validate_changed** | **Before declaring multi-file work complete** - score + gate on changed files. **Always pass explicit `file_paths`** (comma-separated). Default is quick mode; only use `quick=false` as a last resort. |
| **tapps_checklist** | **Before declaring work complete** - reports missing required steps. Response includes an inline `usage_gaps` payload (same data as `tapps_usage`) - read it before declaring done. |
| **tapps_usage** | When you want to see what you missed this session - per-session `gaps` + concrete `recommendations`. Inlined as `usage_gaps` on every `tapps_checklist` response. |
| **tapps_quality_gate** | Before declaring work complete - ensures file passes preset |

**For full tool reference** (44 tools with per-tool guidance), invoke the **tapps-tool-reference** skill when the user asks "what tools does TappsMCP have?", "when do I use tapps_score_file?", etc.

---

## tapps_session_start vs tapps_init

| Aspect | tapps_session_start | tapps_init |
|--------|---------------------|------------|
| **When** | **First call in every session** | **Pipeline bootstrap** (once per project, or when upgrading) |
| **Duration** | Fast (~1s, server info only) | Full run: 10-35+ seconds |
| **Purpose** | Load server info (version, checkers, config) into context | Create files (AGENTS.md, TECH_STACK.md, platform rules), optionally warm cache/RAG |
| **Side effects** | None (read-only) | Writes files, warms caches |
| **Typical flow** | Call at session start, then work | Call once to bootstrap, or `dry_run: true` to preview |

**Session start** -> `tapps_session_start`. Use this as the first call in every session. Returns server info and project context.

**Pipeline/bootstrap** -> `tapps_init`. Use when you need to set up TappsMCP in a project (AGENTS.md, TECH_STACK.md, platform rules) or upgrade existing files.

**Both in one session?** Yes. If the project is not yet bootstrapped: call `tapps_session_start` first (fast), then `tapps_init` (creates files). If the project is already bootstrapped: call only `tapps_session_start` at session start.

**Lighter tapps_init options** (for timeout-prone MCP clients): Use `dry_run: true` to preview (~2-5s); use `verify_only: true` for a quick server/checker check (~1-3s); or set `warm_cache_from_tech_stack: false` and `warm_expert_rag_from_tech_stack: false` for a faster init without cache warming.

**MCP config (default on):** `tapps_init` writes project-scoped MCP config after bootstrap (`mcp_config=true`); strips direct `tapps-brain` entries (bridge-only). Pass `mcp_config=false` to skip. Brain wiring: [docs/operations/CONSUMER-REPO-BRAIN-WIRING.md](docs/operations/CONSUMER-REPO-BRAIN-WIRING.md).

**Tool contract:** Session start returns server info and project context. tapps_validate_changed default = score + gate only; use `security_depth='full'` or `quick=false` for security. tapps_quick_check has no `quick` parameter (use tapps_score_file(quick=True) for that).

---

## Using tapps_lookup_docs for domain guidance

`tapps_lookup_docs` is the primary tool for both library documentation and domain-specific guidance. Pass a `library` name for API docs, or use `topic` to query for patterns and best practices.

| Context | Example call |
|---------|--------------|
| Using an external library | `tapps_lookup_docs(library="fastapi", topic="dependency injection")` |
| Testing patterns | `tapps_lookup_docs(library="pytest", topic="fixtures and parametrize")` |
| Security patterns | `tapps_lookup_docs(library="python-security", topic="input validation")` |
| API design | `tapps_lookup_docs(library="fastapi", topic="routing best practices")` |
| Database patterns | `tapps_lookup_docs(library="sqlalchemy", topic="session management")` |

---

## Recommended workflow

1. **Session start:** Call `tapps_session_start` (returns server info and project context).
2. **Check project memory:** Consider `uv run tapps-mcp memory search --query "..."` or read `.tapps-mcp/session-handoff.md`.
3. **Record key decisions:** Use `tapps_session_notes(action="save", ...)` for session-local notes. Use `uv run tapps-mcp memory save --key ... --tier ... --value "..."` to persist decisions across sessions.
3. **Before using a library:** Call `tapps_lookup_docs(library=...)` and use the returned content when implementing.
4. **Before modifying a file's API:** Call `tapps_impact_analysis(file_path=...)` to see what depends on it.
5. **During edits:** Call `tapps_quick_check(file_path=...)` or `tapps_score_file(file_path=..., quick=True)` after each change.
6. **Before declaring work complete:**
   - Recommended: invoke the `/tapps-finish-task` skill — bundles `tapps_validate_changed` + `tapps_checklist` + an optional memory save and reports a one-line summary.
   - If you'd rather run the steps manually: `tapps_validate_changed(file_paths="file1.py,file2.py")` with explicit paths to score + gate changed files (never call without `file_paths` in large repos; default is quick mode), then `tapps_checklist(task_type=...)` and, if `complete` is false, call the missing required tools (use `missing_required_hints` for reasons). The checklist response also carries an inline `usage_gaps` block — review it for missed lookups or unvalidated edits.
   - Optionally call `tapps_report(format="markdown")` to generate a quality summary.

   **Stop-hook telemetry (warn mode):** if you edited Python/TS/Go files without validating, the Stop hook (`tapps-stop.sh`) appends to `.tapps-mcp/.completion-gate-violations.jsonl`. No block — telemetry that feeds `tapps_usage`. `tapps_doctor` reports `completion_gate_hook.installed`.

   **next_steps shape:** `tapps_score_file` and `tapps_quick_check` template `{file_path}` into next-tool suggestions, so you get paste-ready signatures like `tapps_security_scan(file_path='src/foo.py')`.
7. **When in doubt:** Use `tapps_lookup_docs` for domain-specific questions and library guidance; use `tapps_validate_config` for Docker/infra files.

### Review Pipeline (multi-file)

For reviewing and fixing multiple files in parallel, use the `/tapps-review-pipeline` skill:

1. It detects changed Python files and spawns `tapps-review-fixer` agents (one per file or batch)
2. Each agent scores the file, fixes issues, and runs the quality gate
3. Results are merged and validated with `tapps_validate_changed`
4. A summary table shows before/after scores, gate status, and fixes applied

You can also invoke the `tapps-review-fixer` agent directly on individual files for combined review+fix in a single pass.

---

## Checklist task types

Use the `task_type` that best matches the current work:

- **feature** - New code
- **bugfix** - Fixing a bug
- **refactor** - Refactoring
- **security** - Security-focused change
- **review** - General code review (default)

The checklist uses this to decide which tools are required vs recommended vs optional for that task.

---

## Memory systems

Your project may have two complementary memory systems:

- **Claude Code auto memory** (`~/.claude/projects/<project>/memory/MEMORY.md`): Build commands, IDE preferences, personal workflow notes. Auto-managed.
- **TappsMCP shared memory** — **`uv run tapps-mcp memory`** CLI via BrainBridge (default; do not add direct `tapps-brain` to `.mcp.json`). When **`nlt-memory`** is enabled, `tapps_memory` MCP on that server is a slim facade (TAP-3895). Architecture decisions, quality patterns, cross-agent knowledge. See [docs/MEMORY_REFERENCE.md](docs/MEMORY_REFERENCE.md) and `/tapps-memory` skill.

RECOMMENDED: Use `uv run tapps-mcp memory save|get|search` for architecture decisions and quality patterns. Pin always-on scope keys under `memory_hooks.auto_recall.recall_keys` in `.tapps-mcp.yaml`.

**Access:** Prefer `uv run tapps-mcp memory <subcommand>` (CLI). With `nlt-memory` enabled, `tapps_memory(action=...)` on that server exposes the same actions (TAP-3895). Not on default `nlt-build` alone (TAP-1994).

**Progressive disclosure:** full action catalog, tiers/scopes, brain health fields, and federation details live in the **tapps-memory** skill and [docs/MEMORY_REFERENCE.md](docs/MEMORY_REFERENCE.md). Do not paste the full action list into always-on context.

**Cross-session handoff:** `/tapps-handoff-session` at chat end and `/tapps-continue-session` at chat start (`.tapps-mcp/session-handoff.md` is canonical).

---

## Platform hooks and automation

`tapps_init` / `tapps_upgrade` deploy hooks, subagents, and skills. Keep this file thin — load details on demand:

- **Skills:** invoke `/tapps-finish-task`, `/tapps-memory`, `/tapps-tool-reference`, `linear-issue`, `linear-read` as needed. Set `skill_tier: core` in `.tapps-mcp.yaml` for a smaller inventory.
- **Hooks / subagents / CI:** run `tapps-mcp doctor` for what is wired; engagement level controls hook density.
- **Linear writes:** always use the `linear-issue` skill (never raw `save_issue`). Multi-issue reads: `linear-read`.

> **Removed in v3.12.0:** `tapps-score`, `tapps-gate`, `tapps-validate`, and `tapps-report` wrapper skills were deleted. Prefer direct MCP tool calls or `/tapps-finish-task`.

---

## Troubleshooting: MCP tool permissions

If TappsMCP tools are being rejected or prompting for approval on every call:

**Claude Code:** Ensure `.claude/settings.json` contains **both** permission entries:
```json
{
  "permissions": {
    "allow": [
      "mcp__tapps-mcp",
      "mcp__tapps-mcp__*"
    ]
  }
}
```
The bare `mcp__tapps-mcp` entry is needed as a reliable fallback - the wildcard `mcp__tapps-mcp__*` syntax has known issues in some Claude Code versions (see issues #3107, #13077, #27139). Run `tapps-mcp upgrade --host claude-code` to fix automatically.

**Cursor / VS Code:** These hosts manage MCP tool permissions differently. No `.claude/settings.json` needed.

**If tools are still rejected after fixing permissions:**
1. Restart your MCP host (Claude Code / Cursor / VS Code)
2. Verify the TappsMCP server is running: `tapps-mcp doctor`
3. Check that your permission mode is not `dontAsk` (which auto-denies unlisted tools)
4. As a last resort, use `tapps_quick_check` on individual files instead of `tapps_validate_changed`

---

## Tapps Rules

Seven rules every agent in this project should follow.

1. **Fix root causes, not symptoms.** No workarounds, no `--no-verify`, no try/except-and-swallow. If you are tempted to bypass a failure, stop and diagnose it.
2. **When confidence drops below 100%, query tapps-mcp before writing code.** `tapps_lookup_docs` for library APIs; `uv run tapps-mcp memory search --query "..."` for prior decisions. Guessing from memory is the most common source of hallucinated APIs.
3. **`tapps_lookup_docs` is a Context7-backed cache — use it freely.** Lookups are local-cache-first; repeat calls are near-zero cost. There is no budget to conserve.
4. **Be context-window aware — delegate noisy work to subagents.** If a task would dump more than three file reads or large tool output you won't reference again, spawn `Explore` or `general-purpose`. Subagents return summaries; the main thread stays clean.
5. **Write clean, efficient code.** Clear names, no dead branches, no speculative abstractions, no commented-out code. Every line should justify its presence.
6. **Don't over-engineer.** The simplest solution that satisfies the requirement is the correct one. No knobs nobody asked for. Three similar lines beat a premature abstraction.
7. **Route Linear through skills, not raw plugin calls.** Use the `linear-issue` skill for any write (epic, story, update) — it runs the docs-mcp template + validator before push. Use the `linear-read` skill for multi-issue reads (cache-first). Single-issue lookups: `get_issue(id=...)` directly. Release announcements go through the `linear-release-update` skill.

---

<!-- BEGIN: karpathy-guidelines 2c60614 (MIT, forrestchang/andrej-karpathy-skills) -->
<!--
  Vendored from https://github.com/forrestchang/andrej-karpathy-skills
  Pinned commit: 2c606141936f1eeef17fa3043a72095b4765b9c2 (2026-04-20)
  License: MIT (c) forrestchang
  Do not edit by hand — update KARPATHY_GUIDELINES_SOURCE_SHA in prompt_loader.py
  and re-run the vendor script, then bump tapps-mcp version.
-->
## Karpathy Behavioral Guidelines

> Source: https://github.com/forrestchang/andrej-karpathy-skills @ 2c606141936f1eeef17fa3043a72095b4765b9c2 (MIT)
> Derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls.

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
<!-- END: karpathy-guidelines -->
