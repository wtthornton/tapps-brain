# tapps-brain

Persistent cross-session memory system for AI coding assistants.

A fully deterministic (no LLM calls), SQLite-backed knowledge store with BM25 ranking, exponential decay, automatic consolidation, cross-project federation, and pluggable vector search.

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
from tapps_brain import MemoryStore, MemoryTier

# Create a store (SQLite-backed, persists to .tapps-brain/memory.db)
store = MemoryStore(project_root="/path/to/project")

# Save a memory
store.save(
    key="auth-pattern",
    value="This project uses JWT tokens with refresh rotation",
    tier=MemoryTier.ARCHITECTURAL,
    tags=["auth", "security"],
)

# Search with BM25 ranking
results = store.search("authentication", limit=5)

# Retrieve with automatic decay calculation
entry = store.get("auth-pattern")
```

## Features

- **4 memory tiers** with exponential decay (architectural: 180d, pattern: 60d, procedural: 30d, context: 14d)
- **BM25 ranked retrieval** with composite scoring (relevance + confidence + recency + frequency)
- **Automatic consolidation** of similar memories (Jaccard + TF-IDF cosine)
- **Cross-project federation** via explicit publish/subscribe
- **Garbage collection** with archival (not deletion)
- **RAG safety** checks on all writes and reads
- **Entity relation extraction** with graph-based query expansion
- **Optional hybrid search** (BM25 + vector via Reciprocal Rank Fusion)
- **Import/export** in JSON and Markdown formats

## Architecture

24 focused modules, zero LLM dependencies:

| Module | Purpose |
|---|---|
| `store` | In-memory cache + SQLite write-through |
| `persistence` | SQLite backend with FTS5 |
| `bm25` | Okapi BM25 scorer |
| `decay` | Exponential confidence decay |
| `retrieval` | Ranked retrieval with composite scoring |
| `similarity` | Jaccard + TF-IDF cosine |
| `consolidation` | Deterministic memory merging |
| `federation` | Cross-project sharing hub |
| `gc` | Garbage collection with archival |
| `relations` | Entity/relationship extraction |
| `safety` | RAG content safety checks |

## License

MIT
