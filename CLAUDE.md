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

**Storage layer** — `store.py` is the main `MemoryStore` class: in-memory dict + SQLite write-through, thread-safe via `threading.Lock`. Integrates reinforcement (`reinforce()`), extraction (`ingest_context()`), session indexing (`index_session()`/`search_sessions()`/`cleanup_sessions()`), and doc validation (`validate_entries()` with pluggable `LookupEngineLike`). `persistence.py` handles SQLite with WAL mode, FTS5 full-text search, and schema migrations (v1→v4). JSONL audit log at `{store_dir}/memory/memory_log.jsonl`.

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

### Ralph Files

- `.ralph/PROMPT.md` — High-level goals and instructions for the autonomous agent
- `.ralph/AGENT.md` — Build/test/lint commands Ralph uses
- `.ralph/fix_plan.md` — Prioritized task checklist (Ralph works through this)
- `.ralph/specs/` — Detailed requirement specs
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

### How It Works

Ralph reads `.ralph/PROMPT.md` + `.ralph/fix_plan.md`, invokes Claude Code CLI, analyzes the output, checks progress, and loops until tasks are complete. It includes a circuit breaker to stop if no progress is being made, rate limiting, and session continuity across iterations.

### Important

- **Do not modify** `.ralph/` or `.ralphrc` during a Ralph loop — these are Ralph's control files
- Ralph commits its own changes with descriptive messages referencing stories
- The fix_plan.md is kept in sync with `docs/planning/epics/` priorities
- EPIC-008 (MCP server) is the current top priority in the fix plan
