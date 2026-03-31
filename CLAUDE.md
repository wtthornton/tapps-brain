# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

tapps-brain is a persistent cross-session memory system for AI coding assistants. Fully deterministic (no LLM calls), SQLite-backed knowledge store with BM25 ranking, exponential decay, automatic consolidation, cross-project federation, and pluggable vector search.

## Build & Development Commands

```bash
# Install dependencies (uses uv package manager)
uv sync --extra dev

# Install with optional vector search support
uv sync --extra dev --extra vector

# Run all tests (~2300+ tests, coverage gate ≥95%; exclude benchmarks in CI-style runs)
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

### Source layout: `src/tapps_brain/`

**Storage layer** — `store.py` is the main `MemoryStore` class: in-memory dict + SQLite write-through, thread-safe via `threading.Lock`. Integrates reinforcement (`reinforce()`), extraction (`ingest_context()`), session indexing (`index_session()`/`search_sessions()`/`cleanup_sessions()`), doc validation (`validate_entries()` with pluggable `LookupEngineLike`), **`health()`** / **`get_metrics()`** (observability), feedback APIs (`rate_recall()`, `report_gap()`, `query_feedback()`, …), **`diagnostics()`** / **`diagnostics_history()`**, **`process_feedback()`** / **`generate_report()`** (flywheel), optional Hive propagation (`hive_store` param), and MCP exposure via `mcp_server.py` (tool/resource counts in `docs/generated/mcp-tools-manifest.json`; 3 prompts). `persistence.py` handles SQLite with WAL mode, FTS5 full-text search, and schema migrations (**v1→v11**; v5 = bi-temporal, v6 = tooling bump, v7 = `agent_scope`, v8 = `integrity_hash`, v9 = `feedback_events`, v10 = `diagnostics_history`, v11 = flywheel counts + `flywheel_meta`). JSONL audit log at `{store_dir}/memory/memory_log.jsonl`.

**Data model** — `models.py` defines `MemoryEntry` (Pydantic v2) with tier-based classification (`MemoryTier`: architectural/pattern/procedural/context), source tracking, scope visibility, access counting, and `agent_scope` for Hive propagation. `ConsolidatedEntry` extends it for merged memories. `RecallResult` includes `hive_memory_count` for observability and optional **`quality_warning`** when the diagnostics circuit breaker is not CLOSED.

**Feedback & quality loop** — `feedback.py` (`FeedbackStore`, `FeedbackEvent`) and `diagnostics.py` (composite scorecard, EWMA anomaly detection, circuit breaker) are deterministic. `evaluation.py` (BEIR-style eval harness) and `flywheel.py` (Bayesian confidence updates, gap tracking, markdown reports, optional `LLMJudge` backends) close the improvement loop without requiring LLMs at runtime.

**Retrieval** — `retrieval.py` uses composite scoring: relevance 40%, confidence 30%, recency 15%, frequency 15%. `bm25.py` provides pure-Python Okapi BM25 scoring. `fusion.py` implements Reciprocal Rank Fusion for hybrid BM25 + vector search.

**Memory lifecycle** — `decay.py` applies exponential decay with tier-specific half-lives (architectural: 180d, context: 14d), evaluated lazily on read. `consolidation.py` + `auto_consolidation.py` merge memories deterministically using Jaccard + TF-IDF similarity (no LLM). `gc.py` archives (not deletes) stale memories.

**Safety** — `safety.py` detects prompt injection patterns and sanitizes/blocks RAG content.

**Federation** — `federation.py` enables cross-project memory sharing via a hub at `~/.tapps-brain/memory/federated.db`.

**Hive** — `hive.py` (EPIC-011) enables cross-agent memory sharing via `~/.tapps-brain/hive/hive.db`. `HiveStore` (SQLite, WAL, FTS5, namespace-aware) stores shared memories. `AgentRegistry` (YAML-backed) tracks agent registrations. `PropagationEngine` routes entries to the Hive based on `agent_scope` (`private`/`domain`/`hive`). `ConflictPolicy` resolves concurrent writes (supersede, source_authority, confidence_max, last_write_wins). Hive-aware recall in `recall.py` merges local + Hive results with configurable weight (default 0.8). Library use is opt-in via `hive_store=`; CLI and MCP attach a `HiveStore` by default (see `docs/guides/hive.md`).

**Pluggable extensions** — `_protocols.py` defines Protocol interfaces. Optional deps (faiss, sentence_transformers, cohere) detected lazily via `_feature_flags.py`. Embeddings (`embeddings.py`) and reranking (`reranker.py`) are opt-in.

### Key design decisions

- **Synchronous by design** — no async/await in core code
- **Write-through cache** — all mutations update both in-memory dict and SQLite
- **Lazy decay** — exponential decay computed on read, not via background tasks
- **Deterministic merging** — consolidation uses similarity thresholds, never LLM calls
- **Max 5,000 entries per project** (default; profile-configurable) — enforced in MemoryStore

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
