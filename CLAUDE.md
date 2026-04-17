# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

tapps-brain is a persistent cross-session memory system for AI coding assistants. Fully deterministic (no LLM calls), **PostgreSQL-only** persistence (private memory, Hive, Federation), pgvector HNSW + tsvector hybrid retrieval, BM25 ranking, exponential decay, automatic consolidation, cross-project federation. **SQLite was removed in ADR-007 stage 2 (2026-04-11)** — there is no in-process database fallback.

## Build & Development Commands

```bash
# Install dependencies (uses uv package manager; dev deps are in dependency-groups)
uv sync --group dev

# Optional extras (see pyproject.toml): cli, mcp, reranker, otel, visual, all
# (the legacy `encryption` extra was removed; use pg_tde at the storage layer)

# Run all tests (~2940+ tests, coverage gate ≥95%; exclude benchmarks in CI-style runs)
pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95

# Run a single test file
pytest tests/unit/test_memory_store.py -v

# Run a single test
pytest tests/unit/test_memory_store.py::test_function_name -v

# Lint
ruff check src/ tests/

# Format check
ruff format --check src/ tests/

# Auto-fix lint/format
ruff check --fix src/ tests/
ruff format src/ tests/

# Type check (strict mode)
mypy --strict src/tapps_brain/

# Run benchmarks
pytest tests/benchmarks/ -v --benchmark-only

# Build package
uv build

# Production release gate (packaging, version tests, pytest, ruff, mypy, plugin tests)
# Linux / macOS / WSL: bash scripts/release-ready.sh
# CI uses SKIP_FULL_PYTEST=1 when the test matrix already ran pytest.
# OpenClaw doc drift only: python scripts/check_openclaw_docs_consistency.py
```

## Architecture

**Code-aligned docs** — `docs/engineering/` (system architecture, call flows, schema, optional-feature matrix, inventory).

### Multi-agent architecture (EPIC-053–059, v3.3.0+)

tapps-brain is designed for **many concurrent agents** (200+).  Under ADR-007 every store — private, Hive, Federation — lives in PostgreSQL.  Per-agent isolation is enforced by a `(project_id, agent_id)` composite key on every row, **not** by separate database files.

```
Agent 1 ──┐
Agent 2 ──┤
  ...     ├──► Postgres (private_memories | hive_* | federation_* tables;
Agent N ──┘     pgvector HNSW + tsvector + LISTEN/NOTIFY)
```

- **Private agent memory:** `PostgresPrivateBackend` against the `private_memories` table (migration 001), keyed by `(project_id, agent_id, key)`.  No file-system isolation.
- **Shared memory (Hive):** `PostgresHiveBackend` for cross-agent communication, group knowledge, expert publishing.  Concurrent reads/writes via MVCC, `pgvector` for semantic search, `tsvector` for FTS, `LISTEN/NOTIFY` for change notifications.
- **Federation:** `PostgresFederationBackend` for cross-project memory sharing.
- **Backend abstraction:** `_protocols.py` defines `PrivateBackend`, `HiveBackend`, `FederationBackend`, `AgentRegistryBackend`. `backends.py` provides `create_private_backend(dsn, ...)`, `create_hive_backend(dsn)`, `create_federation_backend(dsn)` factories — every factory requires a `postgres://` or `postgresql://` DSN (ADR-007).
- **AgentBrain facade** (`agent_brain.py`): Simplified 5-method API for agents — `remember()`, `recall()`, `forget()`, `learn_from_success()`, `learn_from_failure()`. Agents never think about backends, scopes, or propagation.

**Key environment variables:**

| Variable | Purpose |
|----------|---------|
| `TAPPS_BRAIN_DATABASE_URL` | Unified Postgres DSN (used for private memory, fallback for Hive). Required at startup — `MemoryStore.__init__` raises `ValueError` if unset. |
| `TAPPS_BRAIN_HIVE_DSN` | Postgres DSN for shared Hive (overrides `TAPPS_BRAIN_DATABASE_URL` for Hive only) |
| `TAPPS_BRAIN_FEDERATION_DSN` | Postgres DSN for Federation |
| `TAPPS_BRAIN_AGENT_ID` | Agent identity string |
| `TAPPS_BRAIN_PROJECT_DIR` | Project root path |
| `TAPPS_BRAIN_GROUPS` | CSV group memberships (e.g. `dev-pipeline,frontend-guild`) |
| `TAPPS_BRAIN_EXPERT_DOMAINS` | CSV expert domains for auto-publish (e.g. `css,react`) |
| `TAPPS_BRAIN_AUTO_MIGRATE` | Set to `1` to auto-apply pending *private* schema migrations at `MemoryStore` startup (STORY-066.8). Default `0`. Raises `MigrationDowngradeError` when the live DB schema exceeds the max bundled version (downgrade guard). **Not recommended for multi-host deployments** — use a one-shot migration job instead. |
| `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | Auto-run Postgres schema migrations on startup |

**Docker deployment:** `docker/docker-compose.hive.yaml` (pgvector/pgvector:pg17), `docker/init-hive.sql`, `docker/Dockerfile.migrate`. See `docs/guides/hive-deployment.md` and `docs/guides/agentforge-integration.md`.

### Source layout: `src/tapps_brain/`

**Agent API** — `agent_brain.py` provides `AgentBrain`, the primary agent-facing class (EPIC-057). Wraps `MemoryStore` + `HiveBackend`. Configured via env vars or constructor args. Context manager support. Agents use this — they never import `MemoryStore` directly.

**Storage layer** — `store.py` is the lower-level `MemoryStore` class: in-memory dict + Postgres write-through, thread-safe via `threading.Lock`. Per-agent isolation via `agent_id` parameter (EPIC-053) — every row in `private_memories` is keyed by `(project_id, agent_id, key)`. Integrates reinforcement (`reinforce()`), extraction (`ingest_context()`), session indexing, doc validation (`validate_entries()`), **`health()`** / **`get_metrics()`** (observability), feedback APIs, **`diagnostics()`**, flywheel, optional Hive propagation (`hive_store` param), groups + expert domains (EPIC-056), and MCP exposure via `mcp_server/` (package with `standard.py` and `operator.py` entry points — STORY-070.9). `postgres_private.py` handles the private memory backend; schema migrations live in `src/tapps_brain/migrations/private/` (currently v1–v5: initial → HNSW upgrade → feedback+session tables → diagnostics history → audit log).  `MemoryStore.__init__` requires a `PrivateBackend` and **raises `ValueError` when neither one is supplied nor a DSN is set** (no SQLite fallback — ADR-007).

**Backend abstraction** — `_protocols.py` defines `PrivateBackend`, `HiveBackend`, `FederationBackend`, `AgentRegistryBackend` Protocol interfaces (EPIC-054 + EPIC-059). `backends.py` provides factory functions (`create_private_backend()`, `create_hive_backend()`, `create_federation_backend()`, `create_agent_registry_backend()`, `resolve_private_backend_from_env()`, `resolve_hive_backend_from_env()`). All durable-store factories require a **PostgreSQL** DSN (`postgres://` or `postgresql://`) — ADR-007. Agent registry may use a YAML file (`FileAgentRegistryBackend`) or Postgres.

**Postgres backends** — `postgres_connection.py` (`PostgresConnectionManager` — connection pooling via `psycopg` + `psycopg_pool`). `postgres_hive.py` (`PostgresHiveBackend` — full `HiveBackend` implementation with parameterized SQL, `pgvector` semantic search, `tsvector` FTS, `LISTEN/NOTIFY`; `PostgresAgentRegistry`). `postgres_federation.py` (`PostgresFederationBackend`). `postgres_migrations.py` (versioned schema migrations for Hive/Federation; SQL files in `src/tapps_brain/migrations/`). All psycopg imports are lazy — Postgres deps only required when using Postgres DSN.

**Data model** — `models.py` defines `MemoryEntry` (Pydantic v2) with tier-based classification (`MemoryTier`: architectural/pattern/procedural/context), source tracking, scope visibility, access counting, and `agent_scope` for Hive propagation. `ConsolidatedEntry` extends it for merged memories. `RecallResult` includes `hive_memory_count` for observability and optional **`quality_warning`** when the diagnostics circuit breaker is not CLOSED.

**Feedback & quality loop** — `feedback.py` (`FeedbackStore`, `FeedbackEvent`) and `diagnostics.py` (composite scorecard, EWMA anomaly detection, circuit breaker) are deterministic. `evaluation.py` (BEIR-style eval harness, plus deterministic `run_consolidation_threshold_sweep` for EPIC-044.4) and `flywheel.py` (Bayesian confidence updates, gap tracking, markdown reports, optional `LLMJudge` backends) close the improvement loop without requiring LLMs at runtime.

**Retrieval** — `retrieval.py` uses composite scoring: relevance 40%, confidence 30%, recency 15%, frequency 15%. `bm25.py` provides pure-Python Okapi BM25 scoring. `fusion.py` implements Reciprocal Rank Fusion for hybrid BM25 + vector search. Optional hybrid pool sizes and RRF *k* are profile-tunable via `MemoryProfile.hybrid_fusion` (YAML `hybrid_fusion:`); `inject_memories` passes this into `MemoryRetriever` when present.

**Memory lifecycle** — `decay.py` applies exponential decay with tier-specific half-lives (architectural: 180d, context: 14d), evaluated lazily on read. `consolidation.py` + `auto_consolidation.py` merge memories deterministically using Jaccard + TF-IDF similarity (no LLM); EPIC-044.4 adds the consolidation audit trail, `MemoryStore.undo_consolidation_merge`, CLI `maintenance consolidation-merge-undo`. `gc.py` archives (not deletes) stale memories. Max-entry eviction: optional **`limits.max_entries_per_group`** (STORY-044.7). Profile **`seeding.seed_version`** labels auto-seed runs (`seeding.py`, EPIC-044.6).

**Safety** — `safety.py` detects prompt injection patterns and sanitizes/blocks RAG content.

**Hive** — Cross-agent memory sharing via PostgreSQL (ADR-007 — Postgres-only; SQLite Hive removed). `postgres_hive.py` (`PostgresHiveBackend`, `PostgresAgentRegistry`). Created via `create_hive_backend(dsn)` with a `postgres://` DSN. `PropagationEngine` routes entries based on `agent_scope` (`private`/`domain`/`hive`). `ConflictPolicy` resolves concurrent writes. Recall merges local + Hive results with configurable weight (default 0.8). Declarative group membership and expert auto-publishing (EPIC-056). See `docs/guides/hive.md`, `docs/guides/hive-deployment.md`.

**Federation** — Cross-project memory sharing via PostgreSQL (ADR-007 — Postgres-only; SQLite Federation removed). `postgres_federation.py` (`PostgresFederationBackend`). Created via `create_federation_backend(dsn)` with a `postgres://` DSN.

**Pluggable extensions** — `_protocols.py` defines Protocol interfaces for backends, embedding providers, rerankers, and LLM judges. Optional deps (flashrank, anthropic, openai, psycopg) detected lazily. Embeddings (`embeddings.py`) and reranking (`reranker.py`) are opt-in.

### Key design decisions

- **Postgres-only persistence** (ADR-007) — every durable store lives in PostgreSQL: private memory, Hive, Federation, audit log, diagnostics history, feedback events, session chunks. No SQLite, no SQLCipher, no in-process fallback.
- **Tenant isolation by row, not by file** — `(project_id, agent_id)` composite key on every private table keeps agents isolated without per-agent database files.
- **pgvector HNSW for semantic recall** (`m=16, ef_construction=200, vector_cosine_ops`) — see migration 002. ~1.5× faster than tuned IVFFlat at comparable recall, no rebuild-after-bulk-load step.
- **tsvector + GIN for lexical recall** with A/B/C weighting on `key` / `value` / `tags`. Upgrade path to ParadeDB `pg_search` (BM25 on Tantivy) when ranking quality matters more than ops simplicity.
- **At-rest encryption is the storage layer's job** — Percona `pg_tde` 2.1.2 (released 2026-03-02) or cloud TDE. Application code does not handle keys.
- **Backend abstraction** — callers program against protocols, never concrete backends; factory selects by DSN.
- **Synchronous by design** — no async/await in core code. `aio.AsyncMemoryStore` (EPIC-067) is a thin `asyncio.to_thread` wrapper for callers that need an async surface; it does not change the sync core.
- **Write-through cache** — all mutations update both in-memory dict and Postgres.
- **Lazy decay** — exponential decay computed on read, not via background tasks.
- **Deterministic merging** — consolidation uses similarity thresholds, never LLM calls.
- **Max 5,000 entries per project** (default; profile-configurable) — enforced in MemoryStore.

## Cross-session memory (tapps-brain MCP)

This repo is wired to the deployed tapps-brain at `http://127.0.0.1:8080/mcp/` as `project_id=tapps-brain`, agent `claude-code-wtthornton`. See [`docs/guides/mcp-client-repo-setup.md`](docs/guides/mcp-client-repo-setup.md) for the wiring and [`docs/guides/claude-code-hooks.md`](docs/guides/claude-code-hooks.md) for the SessionStart hook that auto-primes recall on turn 1.

**Call `brain_recall` when:**
- Starting a session in this repo — recall with the topic the user opens with (architecture, a recent epic, a specific module).
- The user asks "what did we decide about X", "why is Y the way it is", or "have we seen this before".
- You're about to make a non-trivial choice (a new pattern, a deviation from an existing approach) — recall first so prior decisions inform you.

**Call `brain_remember` when:**
- The user corrects your approach or teaches a non-obvious rule.
- A decision is made *with rationale* — the rationale is the memory-worthy part, not the decision itself.
- A debug session reveals a subtle invariant or a surprising constraint that isn't obvious from the code.

**Pick a tier (from the `repo-brain` profile):**
- `architectural` — system decisions, tech-stack choices, infra contracts. Half-life 180 days.
- `pattern` — coding conventions, API shapes, design patterns. 60d.
- `procedural` — workflows, build/deploy commands, runbooks. 30d.
- `context` — session-scope facts; use sparingly, decays in 14d.

Tag important entries with `critical` or `security` for ranking boost.

**Do NOT save:**
- Code patterns / file paths / module layout — derivable by reading the repo.
- Git history, recent diffs, who-changed-what — `git log` / `git blame` are authoritative.
- Ephemeral task state, current-conversation context, debug fix recipes — these belong in `TodoWrite` or the commit message.
- Anything with secrets, tokens, or PII.

**Split with the file-based auto-memory** at `~/.claude/projects/.../memory/`:
- File auto-memory → **user** preferences + **feedback** on how to collaborate with this specific user. Lives across repos.
- tapps-brain MCP → **project** knowledge + **reference** pointers scoped to this repo. Shared across sessions and agents on this project. No manual sync between the two.

## Linear automation (Claude Agent user)

**Status: PLANNED — not yet wired.** Full design in [`docs/guides/linear-claude-agent.md`](docs/guides/linear-claude-agent.md). Read the guide before generating the API key or starting the poller.

Summary: a dedicated Linear user *Claude Agent* (`tapp.thornton+claude@gmail.com`, username `claude`) will be driven by a scheduled poller authed with a Personal API key at `~/.config/claude-agent/linear.env` (chmod 600, never committed). The interactive Linear plugin inside Claude Code stays authed as the operator — only the poller posts as Claude Agent. Trigger convention: `@Claude Agent` in a comment. Dedup: watermark in tapps-brain + in-thread reply check + hidden `<!-- claude-reply:<id> -->` marker.

## Code Quality

- Python 3.12+, strict mypy, ruff with extensive rule set
- Line length: 100 chars
- Tests ignore ANN (annotations) and PLR (pylint refactor) rules
- Coverage minimum: 95%
- LF line endings enforced via `.gitattributes`

## Pre-release and publishing

Before tagging or publishing PyPI / OpenClaw artifacts:

- **Full gate (recommended):** `bash scripts/release-ready.sh` — packaging build, wheel smoke import, version consistency tests, pytest (skip in CI with `SKIP_FULL_PYTEST=1` when the matrix already ran tests), ruff, mypy, `openclaw-plugin` `npm ci` / build / test.
- **OpenClaw docs only:** `python scripts/check_openclaw_docs_consistency.py` — canonical `openclaw plugin install`, SKILL tool/resource counts vs baseline, runbook presence.
- **Checklist:** `scripts/publish-checklist.md`
- **OpenClaw operators:** `docs/guides/openclaw-runbook.md` (PyPI + Git paths), `docs/guides/openclaw.md`

On **Windows**, run the shell gate from **WSL** or **Git Bash** (see `docs/planning/STATUS.md`).

## Planning

Epics and stories live in `docs/planning/epics/` with YAML frontmatter. See `docs/planning/PLANNING.md` for format conventions, templates, and guidance on writing stories that AI assistants can execute. Reference stories in commits: `feat(story-001.3): description`.

Feature intake and triage governance for agents:
- `docs/planning/FEATURE_FEASIBILITY_CRITERIA.md`
- `docs/planning/AGENT_FEATURE_GOVERNANCE.md`
- `docs/planning/ISSUE_TRIAGE_VIEWS.md`

## Ralph (Autonomous Dev Loop)

This project is configured for [Ralph for Claude Code](https://github.com/frankbria/ralph-claude-code) — an autonomous development loop that drives Claude Code CLI through tasks iteratively.

### Ralph Rules

- **Ralph loop only:** `.ralph/fix_plan.md` is the single source of truth for *which task to run next* in that autonomous loop. PROMPT.md defines *how* to work. PROMPT.md must not override fix_plan task order.
- **Product delivery (humans, Cursor, PRs):** canonical queue is `docs/planning/open-issues-roadmap.md` — update that and GitHub; `.ralph/` is **not packaged** and should not be edited for feature bookkeeping unless explicitly syncing Ralph. See `docs/planning/PLANNING.md` (section *Open issues roadmap vs Ralph tooling*).
- Do ONE task per loop from fix_plan.md, in the order listed.
- Do not skip ahead, reorder, or pick tasks from other sources (epics, specs) unless fix_plan.md explicitly references them.
- **Do NOT run pytest, ruff, or mypy mid-epic.** QA is deferred to epic boundaries (when the last `- [ ]` in a `##` section is completed). Set `TESTS_STATUS: DEFERRED` for all mid-epic tasks. This saves 2-5 minutes per loop.

### Ralph Files

- `.ralph/PROMPT.md` — Process instructions for the autonomous agent (NOT priorities)
- `.ralph/AGENT.md` — Build/test/lint commands Ralph uses
- `.ralph/fix_plan.md` — **The priority-ordered task list** (Ralph works through this top to bottom)
- `.ralph/specs/` — Detailed requirement specs (reference only, not task drivers)
- `.ralph/logs/` — Loop execution logs
- `.ralphrc` — Project-level Ralph configuration (rate limits, tool permissions, timeouts)

### Running Ralph

**Always `cd` to this repository’s root first** (the folder that contains `pyproject.toml`). Paths like `/path/to/tapps-brain` in generic guides are placeholders, not real directories. Example if you cloned under `~/code`:

```bash
cd ~/code/tapps-brain
test -f pyproject.toml || { echo "Not the repo root — find the folder with pyproject.toml"; exit 1; }
uv sync --group dev
export PATH="$HOME/.local/bin:$PATH"   # if ralph / claude live here
claude --version    # must be installed; Ralph invokes the Claude Code CLI
```

```bash
# Start the autonomous loop
ralph

# Start with tmux monitoring dashboard (requires tmux)
ralph --monitor

# Start with live streaming output
ralph --live

# Import a PRD or spec into Ralph tasks
ralph-import docs/some-spec.md
```

### Ralph on Windows (use WSL)

Ralph’s global install is bash-based (`~/.ralph/ralph_loop.sh`). **Do not double-click `ralph` or run it from Explorer** — the file has no `.exe`; Windows shows **“Open with…”** instead of executing it.

From Windows, use **WSL** (or Git Bash). Convenience script from the repo (resolves the project path and runs `ralph` inside your default WSL distro):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/Invoke-RalphWsl.ps1 --status
powershell -ExecutionPolicy Bypass -File scripts/Invoke-RalphWsl.ps1 --live
```

Inside WSL directly (same as Linux): ensure `PATH` includes `$HOME/.local/bin`, `cd` to the repo, then `ralph` / `ralph --live`.

1. **Install Ralph inside WSL** (or sync from Windows): copy `C:\Users\<you>\.ralph\` → `~/.ralph/` and `ralph*` wrappers → `~/.local/bin/`, then fix CRLF if copied from Windows:
   `bash scripts/wsl-fix-ralph-crlf.sh`
2. **Dependencies in WSL**: `tmux` (for `--monitor`), `jq`, and `claude` on `PATH`. If `sudo apt install jq` is not an option, install a user-local binary (see `scripts/wsl-verify-ralph.sh`).
3. From the repo: `cd /mnt/c/cursor/tapps-brain` (or your path), ensure `export PATH="$HOME/.local/bin:$PATH"`, then `ralph --live` or `ralph --monitor`.
4. **Upgrade Claude Code in WSL** (if `claude --version` is below 2.0.76 or auto-update hits `EACCES`): in Ubuntu run `sed -i 's/\r$//' scripts/wsl-upgrade-claude-code.sh && bash scripts/wsl-upgrade-claude-code.sh` — installs to `~/.local` (no sudo). Ralph already prepends `~/.local/bin` to `PATH`.
5. **Background from Windows**: run `scripts/wsl-run-ralph-bg.sh` inside WSL (uses **detached `tmux`** so Ralph survives after `wsl.exe` exits; plain `nohup` is killed when the Windows-launched WSL session ends). Log path is printed (`.ralph/logs/tmux-ralph-*.log`). Attach with `tmux attach -t ralph-loop`.

### How It Works

Ralph reads `.ralph/PROMPT.md` + `.ralph/fix_plan.md`, invokes Claude Code CLI, analyzes the output, checks progress, and loops until tasks are complete. It includes a circuit breaker to stop if no progress is being made, rate limiting, and session continuity across iterations.

### Important

- **Do not modify** `.ralph/` or `.ralphrc` during a Ralph loop — these are Ralph's control files
- Ralph commits its own changes with descriptive messages referencing stories
- Open-issues delivery order is tracked in `docs/planning/open-issues-roadmap.md`; Ralph’s `fix_plan.md` should be **reconciled** with that file when starting a Ralph campaign on roadmap work
- Inside Ralph: see fix_plan.md for the next task (not PROMPT.md alone)
