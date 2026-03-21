<p align="center">
  <h1 align="center">tapps-brain</h1>
  <p align="center">
    Persistent cross-session memory for AI coding assistants
    <br />
    <a href="docs/guides/mcp.md"><strong>MCP Server Guide</strong></a>
    &middot;
    <a href="docs/guides/auto-recall.md"><strong>Auto-Recall Guide</strong></a>
    &middot;
    <a href="docs/guides/federation.md"><strong>Federation Guide</strong></a>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/version-1.1.0-blue?style=flat-square" alt="v1.1.0" />
  <img src="https://img.shields.io/badge/tests-1226%20passing-brightgreen?style=flat-square" alt="1226 tests" />
  <img src="https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen?style=flat-square" alt="Coverage 95%" />
  <img src="https://img.shields.io/badge/mypy-strict-blue?style=flat-square" alt="mypy strict" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License" />
</p>

---

A fully deterministic (zero LLM calls), SQLite-backed knowledge store with BM25 ranking, exponential decay, automatic consolidation, cross-project federation, and pluggable vector search. Ships as a **Python library**, **CLI**, and **MCP server** — three first-class interfaces to the same engine.

## Why tapps-brain?

AI coding assistants forget everything between sessions. tapps-brain gives them persistent, ranked memory that decays naturally, consolidates automatically, and works across projects. No LLM needed in the loop — everything is deterministic and reproducible.

**Key differentiators:**

- **Zero async overhead** in the core engine (synchronous by design)
- **No LLM dependency** for any operation (search, decay, consolidation, extraction)
- **Write-through cache** keeps an in-memory dict and SQLite in lockstep
- **Lazy decay** evaluates on read, not via background tasks or cron jobs
- **Pluggable** vector search, reranking, and observability via optional extras

---

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Three Interfaces](#three-interfaces)
- [Core Concepts](#core-concepts)
- [Architecture](#architecture)
- [Development](#development)
- [Documentation](#documentation)
- [License](#license)

---

## Installation

```bash
pip install tapps-brain
```

Optional extras unlock additional capabilities:

```bash
pip install tapps-brain[mcp]       # MCP server for Claude Code, Cursor, VS Code Copilot
pip install tapps-brain[vector]    # Hybrid search (FAISS + sentence-transformers)
pip install tapps-brain[reranker]  # Cohere semantic reranking
pip install tapps-brain[otel]      # OpenTelemetry observability
pip install tapps-brain[all]       # Everything above (except otel)
```

> **Contributors:** `uv sync --extra dev` installs the full dev stack (pytest, ruff, mypy, mcp, typer).

---

## Quick Start

```python
from pathlib import Path
from tapps_brain import MemoryStore

store = MemoryStore(Path("/path/to/project"))

# Save a memory
store.save(
    key="auth-pattern",
    value="This project uses JWT tokens with refresh rotation",
    tier="architectural",
    source="human",
    tags=["auth", "security"],
)

# Search with BM25
results = store.search("authentication")

# Auto-recall: ranked memories ready for prompt injection
result = store.recall("How does auth work?")
print(result.memory_section)   # Formatted context block
print(result.token_count)      # Token budget enforced (default 2000)

# Reinforce a memory that proved useful
store.reinforce("auth-pattern", confidence_boost=0.1)

# Extract facts from conversation automatically
store.ingest_context(
    "We decided to use PostgreSQL. All APIs will be REST, not GraphQL."
)

# Supersede a fact (bi-temporal versioning)
store.supersede(
    old_key="pricing-plan",
    new_value="Pricing is $397/mo (raised from $297)",
    tier="architectural",
)

# Point-in-time query
results = store.search("pricing", as_of="2026-01-15T00:00:00Z")

# Version history
chain = store.history("pricing-plan")

# Session indexing
store.index_session("session-abc", ["Refactored auth middleware", "Added rate limiting"])
hits = store.search_sessions("rate limiting")

store.close()
```

---

## Three Interfaces

tapps-brain exposes the same engine through three equal interfaces:

### Python Library

```python
from tapps_brain import MemoryStore
store = MemoryStore(Path("."))
```

Direct access to all 33 modules. Thread-safe, synchronous, zero setup.

### CLI

```bash
tapps-brain recall "authentication patterns"
tapps-brain store stats --json
tapps-brain memory search "database choice"
tapps-brain maintenance health
tapps-brain federation status
tapps-brain export --format json --output backup.json
```

19 commands across 5 groups: `store`, `memory`, `federation`, `maintenance`, and top-level utilities. All support `--json` output and `--project-dir` override.

### MCP Server

```bash
tapps-brain-mcp --project-dir /path/to/project
```

21 tools, 4 resources, and 3 prompts via the [Model Context Protocol](https://modelcontextprotocol.io/). Works with Claude Code, Cursor, VS Code Copilot, and any MCP-compatible client.

<details>
<summary><strong>MCP client configuration examples</strong></summary>

**Claude Code** (`.mcp.json`):
```json
{
  "mcpServers": {
    "tapps-brain": {
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "/path/to/project"]
    }
  }
}
```

**Cursor** (Settings > MCP or `.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "tapps-brain": {
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "/path/to/project"]
    }
  }
}
```

**VS Code Copilot** (`.vscode/mcp.json`):
```json
{
  "servers": {
    "tapps-brain": {
      "type": "stdio",
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "${workspaceFolder}"]
    }
  }
}
```

</details>

<details>
<summary><strong>Full MCP tool reference</strong></summary>

| Category | Tool | Description |
|----------|------|-------------|
| **Core** | `memory_save` | Save or update a memory entry |
| | `memory_get` | Retrieve a single entry by key |
| | `memory_delete` | Delete an entry by key |
| | `memory_search` | Full-text search with tier/scope/point-in-time filters |
| | `memory_list` | List entries with optional filters |
| **Lifecycle** | `memory_recall` | Auto-recall: ranked memories for a message |
| | `memory_reinforce` | Boost confidence and reset decay |
| | `memory_ingest` | Extract and store facts from text |
| | `memory_supersede` | Create a new version (bi-temporal) |
| | `memory_history` | Show version chain for a key |
| **Session** | `memory_index_session` | Index session chunks for future search |
| | `memory_search_sessions` | Search past session summaries |
| | `memory_capture` | Extract facts from agent response |
| **Federation** | `federation_status` | Hub status and subscriptions |
| | `federation_subscribe` | Subscribe to another project |
| | `federation_unsubscribe` | Remove subscription |
| | `federation_publish` | Publish shared memories to hub |
| **Maintenance** | `maintenance_consolidate` | Merge similar memories |
| | `maintenance_gc` | Archive stale memories |
| | `memory_export` | Export entries as JSON |
| | `memory_import` | Import entries from JSON |

**Resources:** `memory://stats` | `memory://health` | `memory://entries/{key}` | `memory://metrics`

**Prompts:** `recall(topic)` | `store_summary()` | `remember(fact)`

</details>

See the [MCP Server Guide](docs/guides/mcp.md) for detailed setup and usage.

---

## Core Concepts

### Memory Tiers & Decay

Every memory has a tier that determines how quickly it decays:

| Tier | Half-life | Use for |
|------|-----------|---------|
| `architectural` | 180 days | System decisions, tech stack, infrastructure |
| `pattern` | 60 days | Coding conventions, API patterns |
| `procedural` | 30 days | Workflows, deployment steps, processes |
| `context` | 14 days | Session-specific facts, current task details |

Decay is **exponential** and **lazy** (computed on read, no background tasks). Confidence floors prevent total forgetting. Reinforcing a memory resets its decay clock.

### Composite Scoring

Search results are ranked by four weighted signals:

| Signal | Weight | Source |
|--------|--------|--------|
| Relevance | 40% | BM25 full-text match |
| Confidence | 30% | Decayed confidence score |
| Recency | 15% | Time since last update |
| Frequency | 15% | Access count (capped at 20) |

### Scopes

| Scope | Visibility |
|-------|------------|
| `project` | All sessions in this project |
| `branch` | Current git branch only |
| `session` | Current session only (ephemeral) |

### Bi-Temporal Versioning

Facts track **when they were true** (valid_at / invalid_at), not just when they were recorded. `supersede()` atomically invalidates the old version and links to the new one. `search(query, as_of=timestamp)` returns what was known at any point in time.

### Auto-Recall & Capture

```
User message  -->  recall()  -->  Ranked memories injected into prompt
                                        |
Agent response  -->  capture()  -->  New facts extracted and persisted
```

The recall orchestrator searches, ranks, deduplicates, and formats memories within a token budget (default 2000). The capture pipeline extracts decision-like statements from responses and saves them automatically.

### Federation

Share memories across projects via a central hub (`~/.tapps-brain/memory/federated.db`):

```
Project A  --publish-->  Hub  --subscribe-->  Project B
                          |
Project C  --subscribe----+
```

Tag filters and confidence thresholds control what flows between projects.

### Safety

All writes pass through prompt injection detection and content sanitization. The safety layer blocks known injection patterns and sanitizes suspicious content before it enters the store.

---

## Architecture

33 modules, zero LLM dependencies, fully synchronous:

```
                         +------------------+
                         |   Interfaces     |
                         | cli  mcp_server  |
                         +--------+---------+
                                  |
                         +--------v---------+
                         |   MemoryStore    |
                         | (write-through   |
                         |  cache + lock)   |
                         +--------+---------+
                                  |
          +-----------+-----------+-----------+-----------+
          |           |           |           |           |
    +-----v----+ +---v-----+ +--v------+ +--v------+ +--v------+
    | recall   | | search  | | decay   | | safety  | | persist |
    | capture  | | bm25    | | consol. | | inject. | | SQLite  |
    | inject   | | fusion  | | gc      | | sanitiz | | FTS5    |
    +----------+ +---------+ +---------+ +---------+ | WAL    |
                                                      +---------+
          |           |           |
    +-----v----+ +---v-----+ +--v------+
    | embeddings| | federat.| | relations|
    | reranker  | | hub db  | | contrad. |
    | (optional)| |         | |          |
    +----------+ +---------+ +---------+
```

| Layer | Modules | Purpose |
|-------|---------|---------|
| **Storage** | `store`, `persistence` | In-memory dict + SQLite write-through (WAL, FTS5, schema v1-v6) |
| **Data** | `models` | `MemoryEntry` (Pydantic v2) with tiers, sources, scopes, temporal fields |
| **Retrieval** | `retrieval`, `bm25`, `fusion` | Composite-scored ranked search, optional hybrid BM25+vector |
| **Lifecycle** | `decay`, `consolidation`, `auto_consolidation`, `gc` | Exponential decay, Jaccard+TF-IDF merging, archival GC |
| **Recall** | `recall`, `injection` | Orchestrator, capture pipeline, token-budgeted prompt injection |
| **Integrations** | `reinforcement`, `extraction`, `session_index`, `doc_validation` | Boost, fact extraction, session search, doc scoring |
| **Safety** | `safety` | Prompt injection detection, content sanitization |
| **Federation** | `federation` | Cross-project pub/sub via central SQLite hub |
| **Relations** | `relations`, `contradictions` | Entity/relation extraction, contradiction detection |
| **Extensions** | `embeddings`, `reranker`, `similarity` | Optional FAISS vectors, Cohere reranking, TF-IDF similarity |
| **Observability** | `metrics`, `audit`, `otel_exporter` | Counters, latency histograms, JSONL audit log, OpenTelemetry |
| **I/O** | `io`, `seeding` | JSON/Markdown import/export, project profile seeding |
| **Interfaces** | `cli`, `mcp_server` | Typer CLI (19 commands), FastMCP server (21 tools) |
| **Infra** | `_protocols`, `_feature_flags` | Protocol interfaces, lazy optional dependency detection |

### Key Design Decisions

- **Synchronous core** — no async/await anywhere in the engine
- **Write-through cache** — every mutation updates both the in-memory dict and SQLite atomically
- **Lazy decay** — exponential decay evaluated on read, no background tasks or timers
- **Deterministic merging** — consolidation uses Jaccard + TF-IDF similarity thresholds, never LLM calls
- **Max 500 entries** — hard cap per project with lowest-confidence eviction
- **Archive, don't delete** — GC moves stale entries to `archive.jsonl`, never destroys data

---

## Development

### Setup

```bash
# Requires Python 3.12+ and uv
uv sync --extra dev

# With optional vector search
uv sync --extra dev --extra vector
```

### Commands

```bash
# Tests (1226 tests, 95% coverage minimum)
pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95

# Benchmarks
pytest tests/benchmarks/ -v --benchmark-only

# Lint and format
ruff check src/ tests/
ruff format --check src/ tests/

# Type check (strict)
mypy --strict src/tapps_brain/
```

### Test Structure

```
tests/
├── unit/                35 files — pure unit tests, no I/O
├── integration/         11 files — real MemoryStore + SQLite
│   ├── test_retrieval_integration.py
│   ├── test_reinforcement_integration.py
│   ├── test_extraction_integration.py
│   ├── test_session_index_integration.py
│   ├── test_doc_validation_integration.py
│   ├── test_federation_integration.py
│   ├── test_recall_integration.py
│   ├── test_temporal_integration.py
│   ├── test_graph_integration.py
│   ├── test_mcp_integration.py
│   └── test_observability_integration.py
├── benchmarks/          pytest-benchmark performance suite
├── factories.py         Shared make_entry() factory
└── conftest.py          Shared fixtures
```

### Code Quality

| Check | Target | Tool |
|-------|--------|------|
| Tests | 1226 passing | pytest |
| Coverage | >= 95% | pytest-cov |
| Lint | clean | ruff (E, W, F, I, N, UP, ANN, B, A, C4, SIM, TCH, RUF, PLR) |
| Format | 100 char lines | ruff format |
| Types | strict | mypy |
| Line endings | LF | .gitattributes |

---

## Documentation

| Guide | Description |
|-------|-------------|
| [MCP Server Guide](docs/guides/mcp.md) | Client setup for Claude Code, Cursor, VS Code Copilot; full tool/resource/prompt reference |
| [OpenClaw Guide](docs/guides/openclaw.md) | Install, configure, and test tapps-brain with OpenClaw |
| [Auto-Recall Guide](docs/guides/auto-recall.md) | Recall orchestrator usage, configuration, integration patterns |
| [Federation Guide](docs/guides/federation.md) | Cross-project memory sharing setup and operations |
| [Project Status](docs/planning/STATUS.md) | Schema version, dependencies, interfaces, quality gates |
| [Planning Conventions](docs/planning/PLANNING.md) | Epic/story format for AI-assisted development |

<details>
<summary><strong>Epic tracker</strong></summary>

| Epic | Title | Status |
|------|-------|--------|
| [EPIC-001](docs/planning/epics/EPIC-001.md) | Test suite quality | Done |
| [EPIC-002](docs/planning/epics/EPIC-002.md) | Integration wiring | Done |
| [EPIC-003](docs/planning/epics/EPIC-003.md) | Auto-recall + capture pipeline | Done |
| [EPIC-004](docs/planning/epics/EPIC-004.md) | Bi-temporal fact versioning | Done |
| [EPIC-007](docs/planning/epics/EPIC-007.md) | Observability | In progress |
| [EPIC-008](docs/planning/epics/EPIC-008.md) | MCP server (21 tools, 4 resources, 3 prompts) | Done |
| [EPIC-009](docs/planning/epics/EPIC-009.md) | Multi-interface distribution | Planned |

</details>

---

## License

[MIT](LICENSE) &copy; 2025 TappsMCP Contributors
