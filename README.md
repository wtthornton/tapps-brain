# tapps-brain

Persistent cross-session memory system for AI coding assistants.

A fully deterministic (no LLM calls), SQLite-backed knowledge store with BM25 ranking, exponential decay, automatic consolidation, cross-project federation, and pluggable vector search.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![Coverage 95%](https://img.shields.io/badge/coverage-95%25-brightgreen)
![Tests 1039](https://img.shields.io/badge/tests-1039%20passing-brightgreen)
![License MIT](https://img.shields.io/badge/license-MIT-green)

## Installation

```bash
pip install tapps-brain
```

The package includes **Typer** for the CLI (`python -m tapps_brain.cli`). Optional extras:

```bash
pip install tapps-brain[mcp]      # Model Context Protocol server (FastMCP)
pip install tapps-brain[vector]   # FAISS + sentence-transformers + hybrid search
pip install tapps-brain[reranker] # Cohere reranking
```

> **Contributors:** use `uv sync --extra dev` — dev dependencies include `mcp` so MCP unit tests run locally. See [`docs/planning/STATUS.md`](docs/planning/STATUS.md) for a current snapshot.

## Quick Start

```python
from pathlib import Path
from tapps_brain import MemoryStore, MemoryTier

# Create a store (SQLite-backed, persists to .tapps-brain/memory/)
store = MemoryStore(Path("/path/to/project"))

# Save a memory
store.save(
    key="auth-pattern",
    value="This project uses JWT tokens with refresh rotation",
    tier="architectural",
    source="human",
    tags=["auth", "security"],
)

# Search with FTS5
results = store.search("authentication")

# Retrieve with automatic decay calculation
entry = store.get("auth-pattern")

# Reinforce a memory (resets decay clock, optionally boosts confidence)
updated = store.reinforce("auth-pattern", confidence_boost=0.1)

# Extract durable facts from session context automatically
keys = store.ingest_context(
    "We decided to use PostgreSQL for the main database. "
    "Key decision: all APIs will be REST, not GraphQL."
)

# Auto-recall: search and inject relevant memories for a user message
result = store.recall("How does authentication work in this project?")
print(result.memory_section)   # Formatted context block ready for injection
print(result.token_count)      # Token budget enforced (default 2000)

# Supersede a fact (bi-temporal versioning)
new_entry = store.supersede(
    old_key="pricing-plan",
    new_value="Our pricing is $397/mo (raised from $297/mo)",
    tier="architectural",
    source="human",
)

# Point-in-time query: what was true on a past date?
results = store.search("pricing", as_of="2026-01-15T00:00:00Z")

# Trace the full version history of a fact
chain = store.history("pricing-plan")  # Returns [v1, v2, ...] ordered by valid_at

# Index and search past sessions
store.index_session("session-abc", ["Refactored auth middleware", "Added rate limiting"])
results = store.search_sessions("rate limiting")

# Clean up when done
store.close()
```

## Features

### Core

- **4 memory tiers** with exponential decay (architectural: 180d, pattern: 60d, procedural: 30d, context: 14d)
- **BM25 ranked retrieval** with composite scoring (relevance 40% + confidence 30% + recency 15% + frequency 15%)
- **Automatic consolidation** of similar memories (Jaccard + TF-IDF cosine, no LLM)
- **RAG safety** checks on all writes — detects prompt injection and sanitizes content
- **Max 500 entries per project** with automatic eviction of lowest-confidence entries

### Integrations (Epic 2)

- **Memory reinforcement** — `store.reinforce(key)` resets decay clock and optionally boosts confidence
- **Fact extraction** — `store.ingest_context(text)` auto-captures decision-like statements from session context
- **Session indexing** — `store.index_session()` / `search_sessions()` / `cleanup_sessions()` for FTS5 search over past sessions
- **Doc validation** — `store.validate_entries()` scores memories against authoritative docs via pluggable `LookupEngineLike`
- **Cross-project federation** — publish/subscribe model for sharing memories across projects via a central hub

### Auto-Recall (Epic 3)

- **Recall orchestrator** — `store.recall(message)` searches the store and returns injection-ready context with token budget enforcement
- **Capture pipeline** — `store.recall().capture(response)` extracts new facts from agent responses and persists them back to the store
- **Protocol-based hooks** — `RecallHookLike` and `CaptureHookLike` protocols for host agent integration (< 10 lines of glue code)
- **Quality gates** — deduplication window, relevance thresholds, scope/tier/branch filtering, staleness exclusion

### Bi-Temporal Versioning (Epic 4)

- **Validity windows** — `valid_at` / `invalid_at` timestamps on every memory entry (when the fact was true, not just when it was recorded)
- **Supersession** — `store.supersede(old_key, new_value)` atomically invalidates the old fact and links it to the replacement
- **Point-in-time queries** — `store.search(query, as_of="2026-03-01T00:00:00Z")` returns only facts valid at that timestamp
- **History chains** — `store.history(key)` returns the full version chain (all predecessors and successors)
- **Temporal-aware consolidation** — auto-consolidation uses supersession, making merged entries queryable via `history()`

### Extensions

- **Entity relation extraction** with graph-based query expansion
- **Optional hybrid search** (BM25 + vector via Reciprocal Rank Fusion with FAISS)
- **Optional reranking** via Cohere
- **Import/export** in JSON and Markdown formats
- **Garbage collection** with archival (not deletion)

## Architecture

28 focused modules, zero LLM dependencies:

| Layer | Modules | Purpose |
|-------|---------|---------|
| **Storage** | `store`, `persistence` | In-memory cache + SQLite write-through (WAL, FTS5) |
| **Data model** | `models` | `MemoryEntry` (Pydantic v2) with tiers, sources, scopes |
| **Retrieval** | `retrieval`, `bm25`, `fusion` | Ranked search with composite scoring |
| **Lifecycle** | `decay`, `consolidation`, `auto_consolidation`, `gc` | Decay, merging, garbage collection |
| **Auto-Recall** | `recall` | Recall orchestrator, capture pipeline, protocol hooks |
| **Integrations** | `reinforcement`, `extraction`, `session_index`, `doc_validation` | Store-level automation |
| **Safety** | `safety` | RAG content safety and prompt injection detection |
| **Federation** | `federation` | Cross-project memory sharing hub |
| **Relations** | `relations`, `contradictions` | Entity extraction and contradiction detection |
| **Extensions** | `embeddings`, `reranker`, `injection`, `similarity` | Optional vector search, reranking, prompt injection |
| **I/O** | `io`, `seeding` | Import/export, project profile seeding |
| **Interfaces** | `cli`, `mcp_server` | Typer CLI; optional FastMCP server (`mcp` extra) |
| **Infra** | `_protocols`, `_feature_flags` | Protocol interfaces, lazy feature detection |

### Key Design Decisions

- **Synchronous by design** — no async/await in core code
- **Write-through cache** — all mutations update both in-memory dict and SQLite
- **Lazy decay** — exponential decay computed on read, not via background tasks
- **Deterministic merging** — consolidation uses similarity thresholds, never LLM calls
- **Max 500 entries per project** — enforced in MemoryStore with lowest-confidence eviction

## Development

### Setup

```bash
# Requires Python 3.12+ and uv package manager
uv sync --extra dev

# With optional vector search support
uv sync --extra dev --extra vector
```

### Commands

```bash
# Run all tests (~1039 tests)
pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95

# Run benchmarks
pytest tests/benchmarks/ -v --benchmark-only

# Lint and format
ruff check src/ tests/
ruff format --check src/ tests/

# Type check (strict mode)
mypy --strict src/tapps_brain/
```

### Test Structure

```
tests/
├── unit/              # 30+ files, pure unit tests
├── integration/       # End-to-end with real MemoryStore + SQLite
│   ├── test_retrieval_integration.py
│   ├── test_reinforcement_integration.py
│   ├── test_extraction_integration.py
│   ├── test_session_index_integration.py
│   ├── test_doc_validation_integration.py
│   ├── test_federation_integration.py
│   ├── test_recall_integration.py
│   └── test_temporal_integration.py
├── benchmarks/        # pytest-benchmark performance suite
├── factories.py       # Shared make_entry() factory
└── conftest.py        # Shared fixtures
```

### Code Quality

- Python 3.12+, strict mypy, ruff with extensive rule set
- Line length: 100 chars
- Coverage minimum: 95% (see latest `pytest --cov` output)
- LF line endings enforced via `.gitattributes`

## Documentation

- [Project status snapshot](docs/planning/STATUS.md) — Schema version, deps, interfaces, quality gates
- [Federation Guide](docs/guides/federation.md) — Cross-project memory sharing setup and usage
- [Planning Conventions](docs/planning/PLANNING.md) — Epic/story format for AI-assisted development
- [Auto-Recall Guide](docs/guides/auto-recall.md) — Recall orchestrator usage, configuration, and integration examples
- [EPIC-001](docs/planning/epics/EPIC-001.md) — Test suite quality (done)
- [EPIC-002](docs/planning/epics/EPIC-002.md) — Integration wiring (done)
- [EPIC-003](docs/planning/epics/EPIC-003.md) — Auto-recall orchestrator + capture pipeline (done)
- [EPIC-004](docs/planning/epics/EPIC-004.md) — Bi-temporal fact versioning (done)
- [EPIC-007](docs/planning/epics/EPIC-007.md) — Observability (in progress; see STATUS)
- [EPIC-008](docs/planning/epics/EPIC-008.md) — MCP server (in progress; see STATUS)
- [EPIC-009](docs/planning/epics/EPIC-009.md) — Multi-interface distribution (planned)

## License

MIT
