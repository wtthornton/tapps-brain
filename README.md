# tapps-brain

Persistent cross-session memory system for AI coding assistants.

A fully deterministic (no LLM calls), SQLite-backed knowledge store with BM25 ranking, exponential decay, automatic consolidation, cross-project federation, and pluggable vector search.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![Coverage 97%](https://img.shields.io/badge/coverage-97%25-brightgreen)
![Tests 839](https://img.shields.io/badge/tests-839%20passing-brightgreen)
![License MIT](https://img.shields.io/badge/license-MIT-green)

## Installation

```bash
pip install tapps-brain
```

With optional vector search:

```bash
pip install tapps-brain[vector]
```

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

### Extensions

- **Entity relation extraction** with graph-based query expansion
- **Optional hybrid search** (BM25 + vector via Reciprocal Rank Fusion with FAISS)
- **Optional reranking** via Cohere
- **Import/export** in JSON and Markdown formats
- **Garbage collection** with archival (not deletion)

## Architecture

27 focused modules, zero LLM dependencies:

| Layer | Modules | Purpose |
|-------|---------|---------|
| **Storage** | `store`, `persistence` | In-memory cache + SQLite write-through (WAL, FTS5) |
| **Data model** | `models` | `MemoryEntry` (Pydantic v2) with tiers, sources, scopes |
| **Retrieval** | `retrieval`, `bm25`, `fusion` | Ranked search with composite scoring |
| **Lifecycle** | `decay`, `consolidation`, `auto_consolidation`, `gc` | Decay, merging, garbage collection |
| **Integrations** | `reinforcement`, `extraction`, `session_index`, `doc_validation` | Store-level automation |
| **Safety** | `safety` | RAG content safety and prompt injection detection |
| **Federation** | `federation` | Cross-project memory sharing hub |
| **Relations** | `relations`, `contradictions` | Entity extraction and contradiction detection |
| **Extensions** | `embeddings`, `reranker`, `injection`, `similarity` | Optional vector search, reranking, prompt injection |
| **I/O** | `io`, `seeding` | Import/export, project profile seeding |
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
# Run all tests (839 tests, ~35s)
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
│   └── test_federation_integration.py
├── benchmarks/        # pytest-benchmark performance suite
├── factories.py       # Shared make_entry() factory
└── conftest.py        # Shared fixtures
```

### Code Quality

- Python 3.12+, strict mypy, ruff with extensive rule set
- Line length: 100 chars
- Coverage minimum: 95% (current: 97.17%)
- LF line endings enforced via `.gitattributes`

## Documentation

- [Federation Guide](docs/guides/federation.md) — Cross-project memory sharing setup and usage
- [Planning Conventions](docs/planning/PLANNING.md) — Epic/story format for AI-assisted development
- [EPIC-001](docs/planning/epics/EPIC-001.md) — Test suite quality (done: 792 tests, 96.59% coverage)
- [EPIC-002](docs/planning/epics/EPIC-002.md) — Integration wiring (done: 839 tests, 97.17% coverage)

## License

MIT
