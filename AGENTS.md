<!-- tapps-agents-version: 2.4.0 -->
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
| `make brain-up` | Start Postgres+pgvector in the background |
| `make brain-down` | Stop containers and remove volumes |
| `make brain-restart` | Restart the Postgres container (keeps data) |
| `make brain-psql` | Open a psql shell in the running container |
| `make brain-migrate` | Apply all pending schema migrations (idempotent) |
| `make brain-test` | Full test suite with coverage (≥ 95 %) |
| `make brain-test-fast` | Tests excluding benchmarks, no coverage, fail-fast (`-x`) |
| `make brain-lint` | Ruff lint + format check |
| `make brain-type` | Strict mypy type check |
| `make brain-qa` | Full QA: lint + type + tests (mirrors CI) |
| `make publish-brain-image` | Build wheel + `docker-tapps-brain-http:latest` + versioned tag (for AgentForge) |

### DSN override

The default dev DSN is `postgres://tapps:tapps@localhost:5432/tapps_brain_dev` (matches the top-level `docker-compose.yml` quick-start service `tapps-brain-db`).
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

## Ralph (autonomous loop — Linux / Ubuntu)

Run commands from the **repository root** (the directory that contains `pyproject.toml`). Do **not** type a literal path like `/path/to/tapps-brain` — that is only a placeholder in generic docs. Use your real clone path, for example:

```bash
cd ~/code/tapps-brain          # or: cd /home/you/your-clone/tapps-brain
test -f pyproject.toml && echo "OK: repo root" || echo "Wrong directory"
uv sync --group dev
export PATH="$HOME/.local/bin:$PATH"   # so `ralph` and `claude` resolve if installed there
claude --version
ralph                                  # or: ralph --live
```

Ralph reads `.ralph/fix_plan.md` and `.ralph/PROMPT.md`. Logs: `.ralph/logs/`. Full detail: `CLAUDE.md` § Ralph.

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

### Integration test files (STORY-066.13)

| File | Coverage |
|------|----------|
| `tests/integration/test_postgres_private_backend.py` | `PostgresPrivateBackend` save / load_all / delete / search CRUD |
| `tests/integration/test_feedback_postgres.py` | `FeedbackStore` record / query / strict-mode rejection |
| `tests/integration/test_session_index_postgres.py` | `SessionIndex` save_chunks / search / delete_expired |
| `tests/integration/test_agent_identity_postgres.py` | `(project_id, agent_id)` row isolation across multiple agents |
| `tests/integration/test_pgvector_embeddings.py` | pgvector embedding write + knn_search recall |

All tests generate unique `(project_id, agent_id)` pairs per test via `uuid.uuid4()` to
prevent row collisions during parallel test execution.  Teardown is implicit — each test
uses its own rows which never interfere with other tests.

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
| **tapps_checklist** | **Before declaring work complete** - reports missing required steps |
| **tapps_quality_gate** | Before declaring work complete - ensures file passes preset |

**For full tool reference** (26 tools with per-tool guidance), invoke the **tapps-tool-reference** skill when the user asks "what tools does TappsMCP have?", "when do I use tapps_score_file?", etc.

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
2. **Check project memory:** Consider calling `tapps_memory(action="search", query="...")` to recall past decisions and project context.
3. **Record key decisions:** Use `tapps_session_notes(action="save", ...)` for session-local notes. Use `tapps_memory(action="save", ...)` to persist decisions across sessions.
3. **Before using a library:** Call `tapps_lookup_docs(library=...)` and use the returned content when implementing.
4. **Before modifying a file's API:** Call `tapps_impact_analysis(file_path=...)` to see what depends on it.
5. **During edits:** Call `tapps_quick_check(file_path=...)` or `tapps_score_file(file_path=..., quick=True)` after each change.
6. **Before declaring work complete:**
   - Call `tapps_validate_changed(file_paths="file1.py,file2.py")` with explicit paths to score + gate changed files. Never call without `file_paths` in large repos. Default is quick mode; only use `quick=false` as a last resort (pre-release, security audit).
   - Call `tapps_checklist(task_type=...)` and, if `complete` is false, call the missing required tools (use `missing_required_hints` for reasons).
   - Optionally call `tapps_report(format="markdown")` to generate a quality summary.
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
- **TappsMCP shared memory** (`tapps_memory` tool): Architecture decisions, quality patterns, expert findings, cross-agent knowledge. Structured with tiers, confidence decay, contradiction detection, consolidation, and federation.

RECOMMENDED: Use `tapps_memory` for architecture decisions and quality patterns.

### Memory actions (33 total)

**Core:** `save`, `save_bulk`, `get`, `list`, `delete` — CRUD with tier/scope/tag classification (`save` + architectural tier may **supersede** prior versions when `memory.auto_supersede_architectural` is true)

**Search:** `search` — ranked BM25 retrieval with composite scoring (relevance + confidence + recency + frequency)

**Intelligence:** `reinforce`, `gc`, `contradictions`, `reseed`

**Consolidation:** `consolidate`, `unconsolidate`

**Import/export:** `import`, `export`

**Federation:** `federate_register`, `federate_publish`, `federate_subscribe`, `federate_sync`, `federate_search`, `federate_status`

**Maintenance:** `index_session`, `validate`, `maintain`

**Security:** `safety_check`, `verify_integrity`

**Profiles:** `profile_info`, `profile_list`, `profile_switch`

**Diagnostics:** `health`

**Hive / Agent Teams:** `hive_status`, `hive_search`, `hive_propagate`, `agent_register` (opt-in; see `hive_status` when `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is set)

**Default pipeline behavior (POC-oriented):** Shipped config turns on auto-save quality signals, recurring quick_check memory, architectural supersede, impact enrichment, and `memory_hooks` auto-recall/capture — set `false` in `.tapps-mcp.yaml` if you want a quieter setup. See `docs/MEMORY_REFERENCE.md`.

### Memory tiers and scopes

**Tiers:** `architectural` (180-day half-life, stable decisions), `pattern` (60-day, conventions), `procedural` (30-day, workflows), `context` (14-day, short-lived)

**Scopes:** `project` (default, all sessions), `branch` (git branch), `session` (ephemeral), `shared` (federation-eligible)

**Memory profiles:** Built-in profiles from tapps-brain (e.g. `repo-brain` default). Use `profile_info`, `profile_list`, `profile_switch` actions.

**Configuration:** Override `memory.profile`, `memory.capture_prompt`, `memory.write_rules`, and `memory_hooks` in `.tapps-mcp.yaml`. Max 1500 entries per project. Auto-GC at 80% capacity.

---

## Platform hooks and automation

When `tapps_init` generates platform-specific files, it also creates **hooks**, **subagents**, and **skills** that automate parts of the workflow:

### Hooks (auto-generated)

**Claude Code** (`.claude/hooks/`): 7 hook scripts that enforce quality automatically:
- **SessionStart** - Injects TappsMCP awareness on session start and after compaction
- **PostToolUse (Edit/Write)** - Reminds you to run `tapps_quick_check` after Python edits
- **Stop** - Reminds you to run `tapps_validate_changed` before session end (non-blocking)
- **TaskCompleted** - Reminds you to validate before marking task complete (non-blocking)
- **PreCompact** - Backs up scoring context before context window compaction
- **SubagentStart** - Injects TappsMCP awareness into spawned subagents

**Cursor** (`.cursor/hooks/`): 3 hook scripts:
- **beforeMCPExecution** - Logs MCP tool invocations for observability
- **afterFileEdit** - Fire-and-forget reminder to run quality checks
- **stop** - Prompts validation via followup_message before session ends

### Subagents (auto-generated)

Four agent definitions per platform in `.claude/agents/` or `.cursor/agents/`:
- **tapps-reviewer** (sonnet) - Reviews code quality and runs security scans after edits
- **tapps-researcher** (haiku) - Looks up documentation and consults domain experts
- **tapps-validator** (sonnet) - Runs pre-completion validation on all changed files

### Skills (auto-generated)

Twelve SKILL.md files per platform in `.claude/skills/` or `.cursor/skills/`:
- **tapps-score** - Score a Python file across 7 quality categories
- **tapps-gate** - Run a quality gate check and report pass/fail
- **tapps-validate** - Validate all changed files before declaring work complete
- **tapps-review-pipeline** - Orchestrate a parallel review-fix-validate pipeline
- **tapps-research** - Research a technical question using domain experts and docs
- **tapps-security** - Run a comprehensive security audit with vulnerability scanning
- **tapps-memory** - Manage shared project memory for cross-session knowledge

### Agent Teams (opt-in, Claude Code only)

When `tapps_init` is called with `agent_teams=True`, additional hooks enable a quality watchdog teammate pattern:
- **TeammateIdle** - Keeps the quality watchdog active while issues remain
- **TaskCompleted** - Reminds about quality gate validation on task completion

Set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` to enable Agent Teams.

### VS Code / Copilot Instructions (auto-generated)

`.github/copilot-instructions.md` - Provides GitHub Copilot in VS Code with
TappsMCP tool guidance, recommended workflow, and scoring category reference.

### Cursor BugBot Rules (auto-generated, Cursor only)

`.cursor/BUGBOT.md` - Quality standards for Cursor BugBot automated PR review:
security requirements, style rules, testing requirements, and scoring thresholds.

### CI Integration (auto-generated)

`.github/workflows/tapps-quality.yml` - GitHub Actions workflow that validates
changed Python files on every pull request using TappsMCP quality gates.

### MCP Elicitation

When the MCP client supports elicitation (e.g. Cursor), TappsMCP can prompt
the user interactively:
- `tapps_quality_gate` prompts for preset selection when none is provided
- `tapps_init` asks for confirmation before writing configuration files

On unsupported clients, tools fall back to default behavior silently.

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

