<p align="center">
  <h1 align="center">tapps-brain</h1>
  <p align="center">
    Universal persistent memory for AI agents
    <br />
    <a href="docs/guides/profiles.md"><strong>Profile Design Guide</strong></a>
    &middot;
    <a href="docs/guides/profile-catalog.md"><strong>Profile Catalog</strong></a>
    &middot;
    <a href="docs/guides/hive.md"><strong>Hive Guide</strong></a>
    &middot;
    <a href="docs/guides/mcp.md"><strong>MCP Server Guide</strong></a>
    &middot;
    <a href="docs/guides/federation.md"><strong>Federation Guide</strong></a>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/version-1.1.0-blue?style=flat-square" alt="v1.1.0" />
  <img src="https://img.shields.io/badge/profiles-6%20built--in-blueviolet?style=flat-square" alt="6 profiles" />
  <img src="https://img.shields.io/badge/MCP%20tools-28-orange?style=flat-square" alt="28 MCP tools" />
  <img src="https://img.shields.io/badge/tests-1226%20passing-brightgreen?style=flat-square" alt="1226 tests" />
  <img src="https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen?style=flat-square" alt="Coverage 95%" />
  <img src="https://img.shields.io/badge/mypy-strict-blue?style=flat-square" alt="mypy strict" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License" />
</p>

---

A fully deterministic (zero LLM calls), SQLite-backed knowledge store with configurable memory profiles, multi-agent shared brain (Hive), BM25 ranking, dual decay models, automatic consolidation, cross-project federation, and pluggable vector search. Ships as a **Python library**, **CLI**, and **MCP server** — three first-class interfaces to the same engine.

## Why tapps-brain?

AI agents forget everything between sessions. tapps-brain gives them persistent, ranked memory that decays naturally, consolidates automatically, and works across agents and projects. Not limited to code repos — configurable profiles make it a universal brain for **any** AI agent.

### Key Features

| Feature | Description |
|---------|-------------|
| **Configurable Profiles** | Define custom layers, decay models, scoring weights, and promotion rules for any domain. 6 built-in profiles, unlimited custom profiles, YAML inheritance. |
| **Dual Decay Models** | Exponential decay for standard tiers, power-law decay for near-permanent memories. Per-layer half-lives from 1 day to 365+ days. Lazy evaluation on read. |
| **Composite Scoring** | 4-signal ranked retrieval: relevance (BM25) + confidence + recency + frequency. Weights configurable per profile. |
| **Hive (Multi-Agent Brain)** | Cross-agent memory sharing with namespace isolation, 4 conflict resolution policies, and auto-propagation rules. |
| **Federation** | Cross-project memory sharing via pub/sub hub with tag filters and confidence thresholds. |
| **Promotion & Demotion** | Memories move between layers based on access patterns, age, and confidence. Desirable-difficulty bonus and stability growth. |
| **Bi-Temporal Versioning** | Facts track when they were true, not just when recorded. Point-in-time queries and version chains. |
| **Auto-Recall & Capture** | Token-budgeted prompt injection of ranked memories. Automatic fact extraction from agent responses. |
| **Deterministic Everything** | Zero LLM calls for search, decay, consolidation, extraction, or scoring. Fully reproducible. |
| **Safety** | Prompt injection detection and content sanitization on all writes. |

### Key Differentiators

- **Zero async overhead** — synchronous by design, no background tasks
- **Zero LLM dependency** — every operation is deterministic and reproducible
- **Write-through cache** — in-memory dict and SQLite always in lockstep
- **Pluggable extensions** — optional FAISS vectors, Cohere reranking, OpenTelemetry

---

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configurable Profiles](#configurable-profiles)
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

Optional extras:

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

> **New here?** See the [Getting Started guide](docs/guides/getting-started.md) for a use-case map and a 3-line example for each interface (Library / CLI / MCP).

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

# Reinforce a memory that proved useful (can trigger promotion)
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

store.close()
```

---

## Configurable Profiles

Profiles make tapps-brain a universal brain for any AI agent — not just code repos.

### Built-in Profiles

| Profile | Layers | Decay | Scoring emphasis | Use case |
|---------|--------|-------|-----------------|----------|
| **`repo-brain`** | architectural (180d) &rarr; pattern (60d) &rarr; procedural (30d) &rarr; context (14d) | exponential | relevance 40% | Code repos, coding assistants |
| **`personal-assistant`** | identity (365d) &rarr; long-term (90d) &rarr; short-term (7d) &rarr; ephemeral (1d) | **power-law** on identity | recency 30% | Personal AI assistants |
| **`customer-support`** | product-knowledge (120d) &rarr; customer-patterns (60d) &rarr; interaction-history (14d) &rarr; session-context (3d) | exponential | frequency 25% | Support agents, ticketing |
| **`research-knowledge`** | established-facts (365d) &rarr; working-knowledge (60d) &rarr; observations (21d) &rarr; scratch (3d) | **power-law** on facts | relevance 50% | Research, knowledge management |
| **`project-management`** | decisions (180d) &rarr; plans (45d) &rarr; activity (14d) &rarr; noise (5d) | exponential | recency 25% | PM tools, sprint planning |
| **`home-automation`** | household-profile (365d) &rarr; learned-patterns (60d) &rarr; recent-events (7d) &rarr; future-events (90d) &rarr; transient (1d) | **power-law** on household | recency 35% | IoT, smart home |

### Use a Built-in Profile

```python
store = MemoryStore(Path("."), profile_name="personal-assistant")
```

### Create a Custom Profile

Drop a YAML file at `{project}/.tapps-brain/profile.yaml`:

```yaml
profile:
  name: "my-agent"
  version: "1.0"
  description: "Memory for my custom agent"

  layers:
    - name: "core-knowledge"
      description: "Permanent domain facts"
      half_life_days: 365
      decay_model: "power_law"
      decay_exponent: 0.5
      importance_tags:
        critical: 2.0

    - name: "learned-patterns"
      description: "Patterns observed across sessions"
      half_life_days: 60
      promotion_to: "core-knowledge"
      promotion_threshold:
        min_access_count: 15
        min_age_days: 30
        min_confidence: 0.7

    - name: "working-memory"
      description: "Current session context"
      half_life_days: 7
      promotion_to: "learned-patterns"
      promotion_threshold:
        min_access_count: 3
        min_age_days: 2
        min_confidence: 0.4

  scoring:
    relevance: 0.35
    confidence: 0.25
    recency: 0.25
    frequency: 0.15

  hive:
    auto_propagate_tiers: ["core-knowledge"]
    private_tiers: ["working-memory"]
    conflict_policy: "confidence_max"
```

### Inherit and Extend

Override specific parts of a built-in profile without duplicating everything:

```yaml
profile:
  name: "my-variant"
  extends: "repo-brain"
  layers:
    - name: "architectural"
      half_life_days: 365     # longer-lived architecture decisions
  scoring:
    recency: 0.25             # boost recency
    confidence: 0.20          # lower confidence weight
```

> **Full reference:** [Profile Design Guide](docs/guides/profiles.md) &middot; [Profile Catalog](docs/guides/profile-catalog.md)

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

28 tools, 4 resources, and 3 prompts via the [Model Context Protocol](https://modelcontextprotocol.io/). Works with Claude Code, Cursor, VS Code Copilot, and any MCP-compatible client.

<details>
<summary><strong>MCP client configuration</strong></summary>

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
<summary><strong>Full MCP tool reference (28 tools)</strong></summary>

| Category | Tool | Description |
|----------|------|-------------|
| **Core** | `memory_save` | Save or update a memory entry |
| | `memory_get` | Retrieve a single entry by key |
| | `memory_delete` | Delete an entry by key |
| | `memory_search` | Full-text search with tier/scope/point-in-time filters |
| | `memory_list` | List entries with optional filters |
| **Lifecycle** | `memory_recall` | Auto-recall: ranked memories for a message |
| | `memory_reinforce` | Boost confidence, reset decay, may trigger promotion |
| | `memory_ingest` | Extract and store facts from text |
| | `memory_supersede` | Create a new version (bi-temporal) |
| | `memory_history` | Show version chain for a key |
| **Session** | `memory_index_session` | Index session chunks for future search |
| | `memory_search_sessions` | Search past session summaries |
| | `memory_capture` | Extract facts from agent response |
| **Profiles** | `profile_info` | Active profile layers, scoring, and Hive config |
| | `profile_switch` | Switch to a different built-in profile |
| **Hive** | `hive_status` | Namespaces, entry counts, registered agents |
| | `hive_search` | Search across Hive namespaces |
| | `hive_propagate` | Propagate a local memory to the Hive |
| | `agent_register` | Register an agent (id, profile, skills) |
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

### Memory Layers & Decay

Each profile defines **layers** (tiers) with independent decay characteristics. The default `repo-brain` profile:

| Layer | Half-life | Use for |
|-------|-----------|---------|
| `architectural` | 180 days | System decisions, tech stack, infrastructure |
| `pattern` | 60 days | Coding conventions, API patterns |
| `procedural` | 30 days | Workflows, deployment steps, processes |
| `context` | 14 days | Session-specific facts, current task details |

Custom profiles can define **any number of layers** with any names. Two decay models are available:

- **Exponential** (default): `confidence x 0.5^(days / half_life)` — standard, predictable decay
- **Power-law**: `confidence x (1 + days / (9 x half_life))^(-exponent)` — fast initial drop, then near-permanent persistence

Decay is **lazy** — computed on read, no background tasks. Confidence floors prevent total forgetting. **Importance tags** multiply effective half-life (e.g., `safety: 3.0` triples the half-life for safety-tagged memories).

### Promotion & Demotion

Memories move between layers based on usage:

```
ephemeral ──promote──> short-term ──promote──> long-term ──promote──> identity
           (2 accesses,   (5 accesses,           (20 accesses,
            1 day,         3 days,                60 days,
            conf 0.3)      conf 0.5)              conf 0.8)
```

- **Promotion** triggers on reinforcement when access count, age, and confidence all exceed the layer's thresholds
- **Desirable difficulty bonus**: nearly-forgotten memories get bigger boosts when reinforced
- **Stability growth**: reinforced memories decay slower — effective half-life grows with `log1p(reinforce_count)`

### Composite Scoring

Search results are ranked by four weighted signals (configurable per profile):

| Signal | Default | Source |
|--------|---------|--------|
| Relevance | 40% | BM25 full-text match |
| Confidence | 30% | Time-decayed confidence score |
| Recency | 15% | Time since last update |
| Frequency | 15% | Access count (capped) |

Profiles can shift these weights — e.g., personal assistants boost recency (30%), research agents boost relevance (50%), customer support boosts frequency (25%).

### Hive — Multi-Agent Shared Brain

The Hive enables **cross-agent** memory sharing via a central SQLite store with namespace isolation:

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Agent A  │  │ Agent B  │  │ Agent C  │
│ (local)  │  │ (local)  │  │ (local)  │
└────┬─────┘  └────┬─────┘  └────┬─────┘
     │  scope:     │  scope:     │  scope:
     │  domain     │  domain     │  hive
     ▼             ▼             ▼
┌────────────────────────────────────────┐
│              Hive Store               │
│   ┌──────────┐ ┌────┐ ┌───────────┐  │
│   │ agent-a  │ │ agent-b │ │ universal │  │
│   │namespace │ │namespace│ │ namespace │  │
│   └──────────┘ └────┘ └───────────┘  │
└────────────────────────────────────────┘
```

- **Namespace isolation**: each agent writes to its own namespace
- **Cross-namespace search**: query across all or selected namespaces
- **Auto-propagation**: profile config controls which layers propagate
- **4 conflict policies**: supersede, source_authority, confidence_max, last_write_wins
- **Agent registry**: YAML-backed with id, profile, skills, project root

See the [Hive Guide](docs/guides/hive.md).

### Federation

Share memories across **projects** via a central hub:

```
Project A  --publish-->  Hub  --subscribe-->  Project B
                          |
Project C  --subscribe----+
```

Tag filters and confidence thresholds control what flows between projects. See the [Federation Guide](docs/guides/federation.md).

### Bi-Temporal Versioning

Facts track **when they were true** (valid_at / invalid_at), not just when they were recorded. `supersede()` atomically invalidates the old version and links to the new one. `search(query, as_of=timestamp)` returns what was known at any point in time.

### Auto-Recall & Capture

```
User message  -->  recall()  -->  Ranked memories injected into prompt
                                        |
Agent response  -->  capture()  -->  New facts extracted and persisted
```

The recall orchestrator searches, ranks, deduplicates, and formats memories within a configurable token budget (default 2000, up to 3000 for personal-assistant). The capture pipeline extracts decision-like statements from responses and saves them automatically.

### Scopes

| Scope | Visibility |
|-------|------------|
| `project` | All sessions in this project |
| `branch` | Current git branch only |
| `session` | Current session only (ephemeral) |

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
       +----------+------+-------+-------+-----------+
       |          |      |       |       |           |
  +----v---+ +---v--+ +-v----+ +-v----+ +-v-------+ +-v------+
  | recall | |search| |decay | |safety| | persist | |profiles|
  |capture | | bm25 | |promo.| |inject| | SQLite  | | hive   |
  |inject  | |fusion| | gc   | |sanit.| | FTS5    | | agents |
  +--------+ +------+ +------+ +------+ | WAL     | +--------+
                                         +----------+
       |          |          |
  +----v---+ +---v-----+ +-v--------+
  |embedds | |federat. | | relations|
  |reranker| | hub db  | | contrad. |
  |(option)| |         | |          |
  +--------+ +---------+ +----------+
```

| Layer | Modules | Purpose |
|-------|---------|---------|
| **Storage** | `store`, `persistence` | In-memory dict + SQLite write-through (WAL, FTS5, schema v1-v7) |
| **Data** | `models`, `profile` | `MemoryEntry` (Pydantic v2), `MemoryProfile` with configurable layers |
| **Retrieval** | `retrieval`, `bm25`, `fusion` | Composite-scored ranked search, optional hybrid BM25+vector |
| **Lifecycle** | `decay`, `consolidation`, `auto_consolidation`, `gc`, `promotion` | Dual decay models, Jaccard+TF-IDF merging, archival GC, tier promotion |
| **Recall** | `recall`, `injection` | Orchestrator, capture pipeline, token-budgeted prompt injection |
| **Multi-Agent** | `hive` | Hive shared brain, namespace isolation, agent registry, propagation engine |
| **Integrations** | `reinforcement`, `extraction`, `session_index`, `doc_validation` | Boost, fact extraction, session search, doc scoring |
| **Safety** | `safety` | Prompt injection detection, content sanitization |
| **Federation** | `federation` | Cross-project pub/sub via central SQLite hub |
| **Relations** | `relations`, `contradictions` | Entity/relation extraction, contradiction detection |
| **Extensions** | `embeddings`, `reranker`, `similarity` | Optional FAISS vectors, Cohere reranking, TF-IDF similarity |
| **Observability** | `metrics`, `audit`, `otel_exporter` | Counters, latency histograms, JSONL audit log, OpenTelemetry |
| **I/O** | `io`, `seeding` | JSON/Markdown import/export, project profile seeding |
| **Interfaces** | `cli`, `mcp_server` | Typer CLI (19 commands), FastMCP server (28 tools) |
| **Infra** | `_protocols`, `_feature_flags` | Protocol interfaces, lazy optional dependency detection |

### Key Design Decisions

- **Synchronous core** — no async/await anywhere in the engine
- **Write-through cache** — every mutation updates both the in-memory dict and SQLite atomically
- **Lazy decay** — dual-model decay evaluated on read, no background tasks or timers
- **Deterministic merging** — consolidation uses Jaccard + TF-IDF similarity thresholds, never LLM calls
- **Configurable limits** — max entries per profile (default 500, up to 1500+) with lowest-confidence eviction
- **Archive, don't delete** — GC moves stale entries to `archive.jsonl`, never destroys data
- **Profile-driven behavior** — layers, scoring, decay, promotion, GC, and Hive config all come from the active profile

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
| [Getting Started](docs/guides/getting-started.md) | Use-case map and quick example for each interface (Library / CLI / MCP) |
| [Profile Design Guide](docs/guides/profiles.md) | Design custom profiles: layers, decay models, scoring weights, promotion rules, Hive config, anti-patterns |
| [Profile Catalog](docs/guides/profile-catalog.md) | All 6 built-in profiles explained with comparison tables and selection flowchart |
| [Hive Guide](docs/guides/hive.md) | Cross-agent memory sharing: namespaces, propagation, conflict resolution, architecture patterns |
| [MCP Server Guide](docs/guides/mcp.md) | Client setup for Claude Code, Cursor, VS Code Copilot; full 28-tool reference |
| [OpenClaw Guide](docs/guides/openclaw.md) | Install, configure, and test tapps-brain with OpenClaw |
| [Auto-Recall Guide](docs/guides/auto-recall.md) | Recall orchestrator usage, configuration, integration patterns |
| [Federation Guide](docs/guides/federation.md) | Cross-project memory sharing setup and operations |
| [Project Status](docs/planning/STATUS.md) | Schema version, dependencies, interfaces, quality gates |
| [Planning Conventions](docs/planning/PLANNING.md) | Epic/story format for AI-assisted development |
| [Changelog](CHANGELOG.md) | Version history in Keep a Changelog format |

<details>
<summary><strong>Epic tracker</strong></summary>

| Epic | Title | Status |
|------|-------|--------|
| [EPIC-001](docs/planning/epics/EPIC-001.md) | Test suite quality | Done |
| [EPIC-002](docs/planning/epics/EPIC-002.md) | Integration wiring | Done |
| [EPIC-003](docs/planning/epics/EPIC-003.md) | Auto-recall + capture pipeline | Done |
| [EPIC-004](docs/planning/epics/EPIC-004.md) | Bi-temporal fact versioning | Done |
| [EPIC-007](docs/planning/epics/EPIC-007.md) | Observability | Done |
| [EPIC-008](docs/planning/epics/EPIC-008.md) | MCP server (28 tools, 4 resources, 3 prompts) | Done |
| [EPIC-009](docs/planning/epics/EPIC-009.md) | Multi-interface distribution | Done |
| [EPIC-010](docs/planning/epics/EPIC-010.md) | Configurable memory profiles | Done |
| [EPIC-011](docs/planning/epics/EPIC-011.md) | Hive — multi-agent shared brain | Done |
| [EPIC-012](docs/planning/epics/EPIC-012.md) | OpenClaw ContextEngine + ClawHub | Done |

</details>

---

## License

[MIT](LICENSE) &copy; 2025 TappsMCP Contributors
