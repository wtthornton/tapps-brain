<div align="center">

# 🧠 tapps-brain

**Universal persistent memory for AI agents**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Version 1.3.1](https://img.shields.io/badge/version-1.3.1-2ea44f?style=for-the-badge)](https://github.com/wtthornton/tapps-brain/releases)
[![License MIT](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)](LICENSE)
[![Tests 2300+](https://img.shields.io/badge/tests-2300%2B-brightgreen?style=for-the-badge&logo=pytest&logoColor=white)](tests/)
[![Coverage 95%+](https://img.shields.io/badge/coverage-95%25%2B-brightgreen?style=for-the-badge)](pyproject.toml)
[![mypy strict](https://img.shields.io/badge/mypy-strict-blue?style=for-the-badge&logo=python&logoColor=white)](pyproject.toml)

A fully deterministic (zero LLM calls), SQLite-backed knowledge store with configurable memory profiles,<br>multi-agent shared brain (Hive), BM25 ranking, dual decay models, automatic consolidation,<br>cross-project federation, and pluggable vector search.

[Getting Started](docs/guides/getting-started.md) · [Engineering baseline](docs/engineering/README.md) · [Profile Catalog](docs/guides/profile-catalog.md) · [Hive Guide](docs/guides/hive.md) · [MCP Server](docs/guides/mcp.md) · [Federation](docs/guides/federation.md) · [Visual snapshot](docs/guides/visual-snapshot.md) · [OpenClaw](docs/guides/openclaw.md) · [OpenClaw runbook](docs/guides/openclaw-runbook.md)

</div>

---

## Why tapps-brain?

AI agents forget everything between sessions. **tapps-brain** gives them persistent, ranked memory that decays naturally, consolidates automatically, and works across agents and projects — not limited to code repos.

<table>
<tr>
<td width="50%">

### Zero LLM dependency
Every operation — search, decay, consolidation, extraction, scoring — is deterministic and reproducible. No API keys, no latency, no cost.

### Three equal interfaces
Python library, Typer-based CLI (sub-apps for store, memory, feedback, diagnostics, flywheel, Hive, …), and MCP server (tools/resources per [`docs/generated/mcp-tools-manifest.json`](docs/generated/mcp-tools-manifest.json)) — same engine, same behavior, pick what fits your workflow.

</td>
<td width="50%">

### Multi-agent brain (Hive)
Cross-agent memory sharing with namespace isolation, 4 conflict resolution policies, and auto-propagation rules.

### Configurable profiles
6 built-in profiles for any domain. Custom YAML profiles with layers, decay models, scoring weights, and promotion rules.

</td>
</tr>
</table>

## Quick start

```bash
pip install tapps-brain
```

```python
from pathlib import Path
from tapps_brain import MemoryStore

store = MemoryStore(Path("."))

# Save a memory
store.save(
    key="auth-pattern",
    value="This project uses JWT tokens with refresh rotation",
    tier="architectural",
    source="human",
    tags=["auth", "security"],
)

# Recall ranked memories for prompt injection
result = store.recall("How does auth work?")
print(result.memory_section)   # formatted context block
print(result.token_count)      # token budget enforced (default 2000)

store.close()
```

<details>
<summary><strong>More examples</strong></summary>

```python
# Reinforce a useful memory (can trigger promotion)
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
```

</details>

---

## Installation

```bash
pip install tapps-brain                 # core library
pip install tapps-brain[mcp]            # + MCP server for Claude Code, Cursor, VS Code Copilot
pip install tapps-brain[vector]         # + hybrid search (FAISS + sentence-transformers)
pip install tapps-brain[reranker]       # + Cohere semantic reranking
pip install tapps-brain[otel]           # + OpenTelemetry types/helpers (not wired to CLI/MCP yet — see docs/guides/observability.md)
pip install tapps-brain[all]            # everything above (except otel)
```

> **Contributors:** `uv sync --extra dev` installs the full dev stack (pytest, ruff, mypy, mcp, typer).

**Observability note:** [docs/guides/observability.md](docs/guides/observability.md) describes metrics/diagnostics and the OTel module status (EP032).

> **Pre-release / CI parity:** `bash scripts/release-ready.sh` (Linux, macOS, WSL, or Git Bash on Windows) runs packaging, tests, lint, types, and the OpenClaw plugin build. OpenClaw-facing doc drift: `python scripts/check_openclaw_docs_consistency.py`. Details: [`scripts/publish-checklist.md`](scripts/publish-checklist.md), [`docs/planning/STATUS.md`](docs/planning/STATUS.md).

---

## Three interfaces

tapps-brain exposes the same engine through three equal interfaces:

### Python library

```python
from tapps_brain import MemoryStore
store = MemoryStore(Path("."))
```

Direct access to all 38 modules. Thread-safe, synchronous, zero setup.

### CLI — 41 commands

```bash
tapps-brain recall "authentication patterns"
tapps-brain store stats --json
tapps-brain memory search "database choice"
tapps-brain memory tags                          # list all tags
tapps-brain memory audit --last 50               # audit trail
tapps-brain maintenance health
tapps-brain hive status
tapps-brain agent create my-agent --profile repo-brain
tapps-brain federation status
tapps-brain flywheel report --period-days 7
tapps-brain export --format json --output backup.json
```

Typer CLI with multiple sub-apps (`store`, `memory`, `federation`, `maintenance`, `profile`, `hive`, `agent`, `feedback`, `diagnostics`, `flywheel`, `openclaw`, …). Many commands support `--json` output.

### MCP server

```bash
tapps-brain-mcp --project-dir /path/to/project
```

Tool and resource counts are recorded in [`docs/generated/mcp-tools-manifest.json`](docs/generated/mcp-tools-manifest.json) (regenerate: `python scripts/generate_mcp_tool_manifest.py`). The server also exposes 3 prompts via the [Model Context Protocol](https://modelcontextprotocol.io/). Works with Claude Code, Cursor, VS Code Copilot, and any MCP-compatible client.

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

**Cursor** (`.cursor/mcp.json`):
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
| | `memory_reinforce` | Boost confidence, reset decay, may trigger promotion |
| | `memory_ingest` | Extract and store facts from text |
| | `memory_supersede` | Create a new version (bi-temporal) |
| | `memory_history` | Show version chain for a key |
| | `memory_capture` | Extract facts from agent response |
| **Session** | `memory_index_session` | Index session chunks for future search |
| | `memory_search_sessions` | Search past session summaries |
| **Profiles** | `profile_info` | Active profile layers, scoring, and Hive config |
| | `profile_switch` | Switch to a different built-in profile |
| | `memory_profile_onboarding` | Markdown onboarding for the active profile |
| | `profile_tier_migrate` | Remap stored tiers (`tier_map_json`, `dry_run`) |
| **Hive** | `hive_status` | Namespaces, entry counts, registered agents |
| | `hive_search` | Search across Hive namespaces |
| | `hive_propagate` | Propagate a local memory to the Hive |
| | `hive_push` | Batch-promote local memories to the Hive |
| | `hive_write_revision` | Monotonic revision for Hive writes (poll) |
| | `hive_wait_write` | Long-poll wait for Hive revision |
| | `agent_register` | Register an agent (id, profile, skills) |
| | `agent_create` | Composite: register + validate + namespace assignment |
| | `agent_list` | List registered agents |
| | `agent_delete` | Remove an agent registration |
| **Knowledge Graph** | `memory_relations` | Get relations for an entry |
| | `memory_find_related` | BFS traversal from an entity |
| | `memory_query_relations` | Query relation triples |
| **Tags** | `memory_list_tags` | List tags with usage counts |
| | `memory_update_tags` | Add/remove tags on an entry |
| | `memory_entries_by_tag` | List entries that have a tag |
| **Feedback** | `feedback_rate` | Explicit recall quality rating |
| | `feedback_gap` | Report a knowledge gap |
| | `feedback_issue` | Flag a bad entry |
| | `feedback_record` | Custom feedback event type |
| | `feedback_query` | Query stored feedback |
| **Diagnostics** | `diagnostics_report` | Quality scorecard + circuit breaker |
| | `diagnostics_history` | Historical diagnostics snapshots |
| | `tapps_brain_health` | Combined health JSON (store + optional Hive) |
| **Flywheel** | `flywheel_process` | Bayesian feedback → confidence |
| | `flywheel_gaps` | Prioritized knowledge gaps |
| | `flywheel_report` | Markdown quality report |
| | `flywheel_evaluate` | BEIR-style offline eval |
| | `flywheel_hive_feedback` | Hive-wide feedback aggregation |
| **Audit** | `memory_audit` | Query the audit trail |
| **Federation** | `federation_status` | Hub status and subscriptions |
| | `federation_subscribe` | Subscribe to another project |
| | `federation_unsubscribe` | Remove subscription |
| | `federation_publish` | Publish shared memories to hub |
| **Maintenance** | `maintenance_consolidate` | Merge similar memories |
| | `maintenance_gc` | Archive stale memories |
| | `maintenance_stale` | List GC stale candidates with reasons (read-only) |
| | `memory_gc_config` | View GC thresholds |
| | `memory_gc_config_set` | Set GC thresholds |
| | `memory_consolidation_config` | View consolidation config |
| | `memory_consolidation_config_set` | Set consolidation config |
| | `memory_export` | Export entries as JSON |
| | `memory_import` | Import entries from JSON |
| **Session / relay** | `tapps_brain_session_end` | End-of-session episodic summary |
| | `tapps_brain_relay_export` | Build sub-agent relay JSON for import (items may set `memory_group` / `group`; see [memory-relay](docs/guides/memory-relay.md)) |
| **Memory (CLI)** | *(Typer)* `memory save` | Same semantics as MCP `memory_save` — see [Agent integration](docs/guides/agent-integration.md) |
| **OpenClaw** | `openclaw_migrate` | Migrate legacy OpenClaw / plugin data |

**Resources:** `memory://stats` · `memory://health` · `memory://entries/{key}` · `memory://metrics` · `memory://feedback` · `memory://diagnostics` · `memory://report`

**Prompts:** `recall(topic)` · `store_summary()` · `remember(fact)`

</details>

See the [MCP Server Guide](docs/guides/mcp.md) for detailed setup and usage.

---

## Configurable profiles

Profiles make tapps-brain a universal brain for **any** AI agent — not just code repos.

| Profile | Layers | Decay | Scoring emphasis | Use case |
|---------|--------|-------|-----------------|----------|
| **`repo-brain`** | architectural → pattern → procedural → context | exponential | relevance 40% | Code repos, coding assistants |
| **`personal-assistant`** | identity → long-term → short-term → ephemeral | **power-law** on identity | recency 30% | Personal AI assistants |
| **`customer-support`** | product-knowledge → customer-patterns → interaction-history → session-context | exponential | frequency 25% | Support agents, ticketing |
| **`research-knowledge`** | established-facts → working-knowledge → observations → scratch | **power-law** on facts | relevance 50% | Research, knowledge management |
| **`project-management`** | decisions → plans → activity → noise | exponential | recency 25% | PM tools, sprint planning |
| **`home-automation`** | household-profile → learned-patterns → recent-events → future-events → transient | **power-law** on household | recency 35% | IoT, smart home |

```python
# Use a built-in profile
store = MemoryStore(Path("."), profile_name="personal-assistant")
```

<details>
<summary><strong>Create a custom profile</strong></summary>

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

Inherit and override specific parts of a built-in profile:

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

</details>

> **Full reference:** [Profile Design Guide](docs/guides/profiles.md) · [Profile Catalog](docs/guides/profile-catalog.md)

---

## Core concepts

### Memory layers & decay

Each profile defines **layers** (tiers) with independent decay characteristics:

| Layer | Half-life | Use for |
|-------|-----------|---------|
| `architectural` | 180 days | System decisions, tech stack, infrastructure |
| `pattern` | 60 days | Coding conventions, API patterns |
| `procedural` | 30 days | Workflows, deployment steps, processes |
| `context` | 14 days | Session-specific facts, current task details |

Two decay models:
- **Exponential** (default): `confidence × 0.5^(days / half_life)`
- **Power-law**: `confidence × (1 + days / (9 × half_life))^(−exponent)` — near-permanent persistence

Decay is **lazy** — computed on read, no background tasks. **Importance tags** multiply effective half-life.

### Promotion & demotion

Memories move between layers based on usage patterns:

```
context ──promote──▶ procedural ──promote──▶ pattern ──promote──▶ architectural
          (access,      (access,                (access,
           age,          age,                    age,
           confidence)   confidence)             confidence)
```

- **Desirable difficulty bonus**: nearly-forgotten memories get bigger boosts when reinforced
- **Stability growth**: reinforced memories decay slower — effective half-life grows with `log1p(reinforce_count)`

### Composite scoring

Search results are ranked by four weighted signals (configurable per profile):

| Signal | Default | Source |
|--------|---------|--------|
| Relevance | 40% | BM25 full-text match |
| Confidence | 30% | Time-decayed confidence score |
| Recency | 15% | Time since last update |
| Frequency | 15% | Access count (capped) |

### Hive — multi-agent shared brain

Cross-agent memory sharing via a central SQLite store with namespace isolation:

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Agent A  │  │ Agent B  │  │ Agent C  │
│ (local)  │  │ (local)  │  │ (local)  │
└────┬─────┘  └────┬─────┘  └────┬─────┘
     │ scope:      │ scope:      │ scope:
     │ domain      │ domain      │ hive
     ▼             ▼             ▼
┌────────────────────────────────────────┐
│             Hive Store                 │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │ agent-a │ │ agent-b │ │universal│  │
│  │namespace│ │namespace│ │namespace│  │
│  └─────────┘ └─────────┘ └─────────┘  │
└────────────────────────────────────────┘
```

4 conflict policies: `supersede` · `source_authority` · `confidence_max` · `last_write_wins`

See the [Hive Guide](docs/guides/hive.md).

### Federation

Share memories across **projects** via a central hub with tag filters and confidence thresholds.

```
Project A  ──publish──▶  Hub  ◀──subscribe──  Project B
                          │
Project C  ──subscribe────┘
```

See the [Federation Guide](docs/guides/federation.md).

### Bi-temporal versioning

Facts track **when they were true** (valid_at / invalid_at), not just when recorded. `supersede()` atomically invalidates the old version and links to the new one. `search(query, as_of=timestamp)` returns what was known at any point in time.

### Safety

All writes pass through prompt injection detection and content sanitization. The safety layer blocks known injection patterns and sanitizes suspicious content before it enters the store.

---

## Architecture

38 modules, zero LLM dependencies, fully synchronous:

```
                         ┌──────────────────┐
                         │    Interfaces     │
                         │  CLI · MCP · Lib  │
                         └────────┬─────────┘
                                  │
                         ┌────────▼─────────┐
                         │   MemoryStore     │
                         │  (write-through   │
                         │   cache + lock)   │
                         └────────┬─────────┘
                                  │
       ┌──────────┬───────┬───────┼───────┬───────────┐
       │          │       │       │       │           │
  ┌────▼───┐ ┌───▼──┐ ┌──▼───┐ ┌─▼────┐ ┌─▼───────┐ ┌▼───────┐
  │ recall │ │search│ │decay │ │safety│ │ persist │ │profiles│
  │capture │ │ bm25 │ │promo │ │inject│ │ SQLite  │ │  hive  │
  │inject  │ │fusion│ │  gc  │ │sanit │ │  FTS5   │ │ agents │
  └────────┘ └──────┘ └──────┘ └──────┘ │  WAL    │ └────────┘
                                         └─────────┘
       │          │          │
  ┌────▼───┐ ┌───▼─────┐ ┌──▼────────┐
  │embedds │ │federat. │ │ relations │
  │reranker│ │  hub db │ │ contrad.  │
  │(option)│ │         │ │           │
  └────────┘ └─────────┘ └───────────┘
```

<details>
<summary><strong>Module map</strong></summary>

| Layer | Modules | Purpose |
|-------|---------|---------|
| **Storage** | `store`, `persistence` | In-memory dict + SQLite write-through (WAL, FTS5, schema v1–v11) |
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
| **Observability** | `metrics`, `audit`, `diagnostics`, `feedback`, `evaluation`, `flywheel`, `otel_exporter` | Counters, audit, quality scorecard, feedback store, eval/flywheel loop, optional OTel |
| **I/O** | `io`, `seeding` | JSON/Markdown import/export, project profile seeding |
| **Interfaces** | `cli`, `mcp_server` | Typer CLI (multi sub-app), FastMCP server (counts in `docs/generated/mcp-tools-manifest.json`) |
| **Infra** | `_protocols`, `_feature_flags` | Protocol interfaces, lazy optional dependency detection |

</details>

### Key design decisions

- **Synchronous core** — no async/await anywhere in the engine
- **Write-through cache** — every mutation updates both the in-memory dict and SQLite atomically
- **Lazy decay** — dual-model decay evaluated on read, no background tasks or timers
- **Deterministic merging** — consolidation uses Jaccard + TF-IDF similarity thresholds, never LLM calls
- **Configurable limits** — max entries per profile (default 500, up to 1500+) with lowest-confidence eviction
- **Archive, don't delete** — GC moves stale entries to `archive.jsonl`, never destroys data
- **Profile-driven behavior** — layers, scoring, decay, promotion, GC, and Hive config all come from the active profile

---

## Development

```bash
# Requires Python 3.12+ and uv
uv sync --extra dev

# Tests (~2300+ collected; coverage gate ≥95%; benchmarks excluded like CI release gate)
pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95

# Lint + format
ruff check src/ tests/ && ruff format --check src/ tests/

# Type check (strict)
mypy --strict src/tapps_brain/

# Benchmarks
pytest tests/benchmarks/ -v --benchmark-only

# Production release gate (also what CI job `release-ready` runs, with SKIP_FULL_PYTEST=1 there)
bash scripts/release-ready.sh
```

<details>
<summary><strong>Test structure</strong></summary>

```
tests/
├── unit/                35+ files — pure unit tests, no I/O
├── integration/         11+ files — real MemoryStore + SQLite
├── benchmarks/          pytest-benchmark performance suite
├── factories.py         Shared make_entry() factory
└── conftest.py          Shared fixtures
```

</details>

| Check | Target | Tool |
|-------|--------|------|
| Tests | ~2300+ collected | pytest |
| Coverage | ≥ 95% | pytest-cov |
| Lint | clean | ruff |
| Format | 100 char lines | ruff format |
| Types | strict | mypy |
| Line endings | LF | .gitattributes |
| Release gate | green before publish | `scripts/release-ready.sh` |
| OpenClaw docs | no install/count drift | `scripts/check_openclaw_docs_consistency.py` |

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/guides/getting-started.md) | Use-case map and quick example for each interface |
| [Profile Design Guide](docs/guides/profiles.md) | Custom profiles: layers, decay, scoring, promotion, Hive config |
| [Profile Catalog](docs/guides/profile-catalog.md) | All 6 built-in profiles with comparison tables |
| [Hive Guide](docs/guides/hive.md) | Cross-agent memory sharing: namespaces, propagation, conflict resolution |
| [MCP Server Guide](docs/guides/mcp.md) | Client setup for Claude Code, Cursor, VS Code Copilot; full tool reference |
| [OpenClaw Guide](docs/guides/openclaw.md) | Install, configure, and test with OpenClaw |
| [OpenClaw runbook](docs/guides/openclaw-runbook.md) | Canonical PyPI + Git install, upgrade, verify, restart |
| [Auto-Recall Guide](docs/guides/auto-recall.md) | Recall orchestrator usage and integration patterns |
| [Publish checklist](scripts/publish-checklist.md) | PyPI pre-flight (includes release gate command) |
| [Federation Guide](docs/guides/federation.md) | Cross-project memory sharing setup |
| [Changelog](CHANGELOG.md) | Version history |

<details>
<summary><strong>Epic tracker (selected)</strong></summary>

| Epic | Title | Status |
|------|-------|--------|
| [EPIC-001](docs/planning/epics/EPIC-001.md)–[016](docs/planning/epics/EPIC-016.md) | Core platform (tests through Hive hardening) | Done |
| [EPIC-008](docs/planning/epics/EPIC-008.md) | MCP server | Done (surface 64 tools / 8 resources — see [MCP guide](docs/guides/mcp.md)) |
| [EPIC-029](docs/planning/epics/EPIC-029.md) | Feedback collection | Done |
| [EPIC-030](docs/planning/epics/EPIC-030.md) | Diagnostics & self-monitoring | Done |
| [EPIC-031](docs/planning/epics/EPIC-031.md) | Continuous improvement flywheel | Done |
| [EPIC-032](docs/planning/epics/EPIC-032.md) | OTel GenAI conventions | Planned |
| [EPIC-033](docs/planning/epics/EPIC-033.md) | OpenClaw plugin SDK alignment | Done |
| [EPIC-034](docs/planning/epics/EPIC-034.md)–[036](docs/planning/epics/EPIC-036.md) | Production QA, OpenClaw doc consistency, release gate | Done |

See [`docs/planning/STATUS.md`](docs/planning/STATUS.md) and [`docs/planning/epics/`](docs/planning/epics/) for the full list (including code-review epics 017–025).

</details>

---

## License

[MIT](LICENSE) &copy; 2025 TappsMCP Contributors
