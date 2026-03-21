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

# Run all tests
pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95

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
```

## Architecture

### Source layout: `src/tapps_brain/`

**Storage layer** — `store.py` is the main `MemoryStore` class: in-memory dict + SQLite write-through, thread-safe via `threading.Lock`. Integrates reinforcement (`reinforce()`), extraction (`ingest_context()`), session indexing (`index_session()`/`search_sessions()`/`cleanup_sessions()`), doc validation (`validate_entries()` with pluggable `LookupEngineLike`), **`health()`** / **`get_metrics()`** (observability), and MCP exposure via `mcp_server.py` (21 tools including session index/search/capture, 4 resources, 3 prompts). `persistence.py` handles SQLite with WAL mode, FTS5 full-text search, and schema migrations (**v1→v6**; v5 = bi-temporal columns, v6 = version bump for tooling). JSONL audit log at `{store_dir}/memory/memory_log.jsonl`.

**Data model** — `models.py` defines `MemoryEntry` (Pydantic v2) with tier-based classification (`MemoryTier`: architectural/pattern/procedural/context), source tracking, scope visibility, and access counting. `ConsolidatedEntry` extends it for merged memories.

**Retrieval** — `retrieval.py` uses composite scoring: relevance 40%, confidence 30%, recency 15%, frequency 15%. `bm25.py` provides pure-Python Okapi BM25 scoring. `fusion.py` implements Reciprocal Rank Fusion for hybrid BM25 + vector search.

**Memory lifecycle** — `decay.py` applies exponential decay with tier-specific half-lives (architectural: 180d, context: 14d), evaluated lazily on read. `consolidation.py` + `auto_consolidation.py` merge memories deterministically using Jaccard + TF-IDF similarity (no LLM). `gc.py` archives (not deletes) stale memories.

**Safety** — `safety.py` detects prompt injection patterns and sanitizes/blocks RAG content.

**Federation** — `federation.py` enables cross-project memory sharing via a hub at `~/.tapps-brain/memory/federated.db`.

**Pluggable extensions** — `_protocols.py` defines Protocol interfaces. Optional deps (faiss, sentence_transformers, cohere) detected lazily via `_feature_flags.py`. Embeddings (`embeddings.py`) and reranking (`reranker.py`) are opt-in.

### Key design decisions

- **Synchronous by design** — no async/await in core code
- **Write-through cache** — all mutations update both in-memory dict and SQLite
- **Lazy decay** — exponential decay computed on read, not via background tasks
- **Deterministic merging** — consolidation uses similarity thresholds, never LLM calls
- **Max 500 entries per project** — enforced in MemoryStore

## Code Quality

- Python 3.12+, strict mypy, ruff with extensive rule set
- Line length: 100 chars
- Tests ignore ANN (annotations) and PLR (pylint refactor) rules
- Coverage minimum: 95%
- LF line endings enforced via `.gitattributes`

## Planning

Epics and stories live in `docs/planning/epics/` with YAML frontmatter. See `docs/planning/PLANNING.md` for format conventions, templates, and guidance on writing stories that AI assistants can execute. Reference stories in commits: `feat(story-001.3): description`.

## Ralph (Autonomous Dev Loop)

This project is configured for [Ralph for Claude Code](https://github.com/frankbria/ralph-claude-code) — an autonomous development loop that drives Claude Code CLI through tasks iteratively.

### Ralph Rules

- **fix_plan.md is the single source of truth for task priority.** PROMPT.md defines *how* to work (rules, constraints, process). fix_plan.md defines *what* to work on (priorities, order). PROMPT.md must NEVER override or restate priorities — always defer to fix_plan.md for task selection.
- Do ONE task per loop from fix_plan.md, in the order listed.
- Do not skip ahead, reorder, or pick tasks from other sources (epics, specs) unless fix_plan.md explicitly references them.

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
- The fix_plan.md is kept in sync with `docs/planning/epics/` priorities
- See fix_plan.md for current task priorities (not PROMPT.md or epic files)
