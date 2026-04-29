# tapps-brain

## Why tapps-brain?

AI agents forget everything between sessions. **tapps-brain** gives them persistent, ranked memory that decays naturally, consolidates automatically, and works across agents and projects — not limited to code repos.

<table>
<tr>
<td width="50%">

### Zero LLM dependency
Every operation — search, decay, consolidation, extraction, scoring — is deterministic and reproducible. No API keys, no latency, no cost.

### Two deployment interfaces
**Deployed container** (`docker-tapps-brain-http`) — one container per host, all agents connect via MCP Streamable HTTP (`mcp+http://`) or REST (`http://`). **Python library** — embed `MemoryStore` / `AgentBrain` directly in-process. Same engine, same behavior. MCP tools manifest: [`docs/generated/mcp-tools-manifest.json`](docs/generated/mcp-tools-manifest.json).

</td>
<td width="50%">

### Multi-agent brain (Hive)
Cross-agent memory sharing with namespace isolation, 4 conflict resolution policies, and auto-propagation rules.

### Configurable profiles
6 built-in profiles for any domain. Custom YAML profiles with layers, decay models, scoring weights, and promotion rules.

</td>
</tr>
</table>

## See it in action

The **brain-visual** dashboard shows your memory store at a glance — tier mix, scorecard health, retrieval stack, Hive status, agent topology, tag cloud, and diagnostics — no code required. It polls the live `/snapshot` endpoint exposed by the tapps-brain HTTP adapter.

```bash
docker compose -f docker/docker-compose.hive.yaml up -d --build

```

The dashboard polls `/snapshot` every 30 seconds (configurable) and shows a **LIVE / STALE / OFFLINE / ERROR** connection badge with a last-refreshed timestamp. There is no file-load or demo fallback — if the endpoint is unreachable, start the adapter.

→ [Visual snapshot guide](docs/guides/visual-snapshot.md) · [Dashboard README](examples/brain-visual/README.md)

---

## Connect a coding project

Drop tapps-brain into an existing project in one command:

```bash
cd your-project
tapps-brain init                 # writes .mcp.json, brain_init.py, profile.yaml, .env.example
```

That scaffold gives you two independent entry points:

- **Design-time** — a `.mcp.json` that points your IDE's coding agent (Claude Code, Cursor) at the deployed tapps-brain hub via MCP. Save/recall memories as you code.
- **Runtime** — a `brain_init.py` factory for embedding `AgentBrain` in your shipped app's agent loop.

The scaffold lives at [examples/coding-project-init/](examples/coding-project-init/) if you'd rather copy files manually. See its [README](examples/coding-project-init/README.md) for the full walkthrough and for how the two entry points differ.

---

## Quick start

**Contributors (Cursor / VS Code):** after clone, see [AGENTS.md](AGENTS.md) for `uv sync`, tests, and pointers to `.vscode/` tasks and `.cursor/mcp.json`.

> **PostgreSQL is required.** As of [ADR-007](docs/planning/adr/ADR-007-postgres-only-no-sqlite.md) (2026-04-11), tapps-brain is **Postgres-only** — there is no SQLite or in-process fallback.  `MemoryStore.__init__` raises `ValueError` if `TAPPS_BRAIN_DATABASE_URL` is unset and no explicit `private_backend` is supplied.  For local dev, run `make brain-up` to start the bundled `pgvector/pg17` container.

```bash
docker compose -f docker/docker-compose.hive.yaml up -d
export TAPPS_BRAIN_DATABASE_URL=postgresql://tapps:tapps@localhost:5432/tapps_brain
```

**Python**

```bash
pip install tapps-brain
```

```python
from pathlib import Path
from tapps_brain import MemoryStore

store = MemoryStore(Path("."))

store.save(
    key="auth-pattern",
    value="This project uses JWT tokens with refresh rotation",
    tier="architectural",
    source="human",
    tags=["auth", "security"],
)

result = store.recall("How does auth work?")
print(result.memory_section)   # formatted context block
print(result.token_count)      # token budget enforced (default 2000)

store.close()
```

**TypeScript / Node.js**

```bash
npm install @tapps-brain/sdk
```

```typescript
import { TappsBrainClient } from "@tapps-brain/sdk";

const brain = new TappsBrainClient({
  url: "http://localhost:8080",
  projectId: "my-project",
  agentId: "my-agent",
  authToken: process.env.TAPPS_BRAIN_AUTH_TOKEN,
});

// Save a fact
await brain.remember("Prefer ruff over pylint for linting", { tier: "pattern" });

// Recall relevant memories (BM25 + vector hybrid)
const memories = await brain.recall("linting conventions");
for (const m of memories) {
  console.log(`[${m.tier}] ${m.key}: ${m.value}`);
}

brain.close();
```

Full guides: [TypeScript SDK](docs/guides/typescript-sdk.md) · [LangGraph Store adapter](docs/guides/langgraph-adapter.md)

> **Dev tip:** set `TAPPS_BRAIN_AUTO_MIGRATE=1` to enable auto-migration of the private schema on startup — no need to run `make brain-migrate` manually in local dev. In production, run migrations explicitly before deploying.

<details>
<summary><strong>More Python examples</strong></summary>

```python
store.reinforce("auth-pattern", confidence_boost=0.1)

store.ingest_context(
    "We decided to use PostgreSQL. All APIs will be REST, not GraphQL."
)

store.supersede(
    old_key="pricing-plan",
    new_value="Pricing is $397/mo (raised from $297)",
    tier="architectural",
)

results = store.search("pricing", as_of="2026-01-15T00:00:00Z")

chain = store.history("pricing-plan")
```

</details>

---

## What's new in v3.5.1

- **Multi-tenant project registration (EPIC-069 / ADR-010):** one shared `tapps-brain` deployment serves many client projects with per-project profiles and real data isolation. `project_id` travels on every request (`_meta.project_id` > `X-Tapps-Project` header > `TAPPS_BRAIN_PROJECT` env > `"default"`), profiles live in a Postgres `project_profiles` registry, per-call MCP dispatch via a bounded LRU store cache, structured rejection errors in strict mode, and RLS on `private_memories` / `project_profiles` (migration 009) as defence-in-depth.
- **Postgres production-readiness (EPIC-066):** ephemeral-Postgres CI, connection pool health in `/health`, `TAPPS_BRAIN_AUTO_MIGRATE=1` startup gate, pg_tde encryption runbook, and behavioural parity load smoke against 50 concurrent agents.
- **Live always-on dashboard (EPIC-065):** GET `/snapshot` endpoint on the HTTP adapter; dashboard polls every 5 s with LIVE/STALE/ERROR badge; Hive hub deep monitoring panel and agent registry live table.

---

## Installation

```bash
pip install tapps-brain                 # core library (includes psycopg[binary] + sentence-transformers)
pip install tapps-brain[mcp]            # + MCP server for Claude Code, Cursor, VS Code Copilot
pip install tapps-brain[reranker]       # + FlashRank local reranking (no API key needed)
pip install tapps-brain[visual]         # + Playwright headless PNG capture (tapps-brain visual capture)
pip install tapps-brain[otel]           # + OpenTelemetry types/helpers (not wired to CLI/MCP yet — see docs/guides/observability.md)
pip install tapps-brain[all]            # everything above (except visual and otel)
```

> **PostgreSQL backend.** Vector ANN is **pgvector HNSW** (`m=16, ef_construction=200`); lexical retrieval is `tsvector` + GIN with A/B/C weighting; at-rest encryption is delegated to the storage layer (Percona `pg_tde` 2.1.2 or cloud TDE). The historical SQLite, `sqlite-vec`, and SQLCipher dependencies were removed in [ADR-007](docs/planning/adr/ADR-007-postgres-only-no-sqlite.md) stage 2.

> **Visual PNG capture:** after `pip install tapps-brain[visual]`, also run `playwright install chromium` once to download the browser binary. See [Visual snapshot guide](docs/guides/visual-snapshot.md).

> **Contributors:** `uv sync --group dev` installs the full dev stack (pytest, ruff, mypy, mcp, typer).

**Observability note:** [docs/guides/observability.md](docs/guides/observability.md) describes metrics/diagnostics and the OTel module status (EP032).

> **Pre-release / CI parity:** `bash scripts/release-ready.sh` (Linux, macOS, WSL, or Git Bash on Windows) runs packaging, tests, lint, types, and the OpenClaw plugin build. OpenClaw-facing doc drift: `python scripts/check_openclaw_docs_consistency.py`. Details: [`scripts/publish-checklist.md`](scripts/publish-checklist.md), [`docs/planning/STATUS.md`](docs/planning/STATUS.md).

> **Distribution (TAP-992):** Releases are published as GitHub Release artifacts on every `vX.Y.Z` tag push via `.github/workflows/release.yml`. Consumers that previously used `vendor/*.whl` should switch to:
> ```toml
> # pyproject.toml (uv-compatible)
> tapps-brain = { url = "https://github.com/wtthornton/tapps-brain/releases/download/vX.Y.Z/tapps_brain-X.Y.Z-py3-none-any.whl" }
> ```
> See [`scripts/publish-checklist.md`](scripts/publish-checklist.md) and [`docs/guides/openclaw-runbook.md`](docs/guides/openclaw-runbook.md) (Path B).

---

## Three interfaces

tapps-brain exposes the same engine through three equal interfaces:

### Python library

```python
from tapps_brain import MemoryStore
store = MemoryStore(Path("."))
```

Direct access to all modules. Thread-safe, synchronous, zero setup.

### CLI — 43 commands

```bash
tapps-brain recall "authentication patterns"
tapps-brain store stats --json
tapps-brain memory search "database choice"
tapps-brain memory tags                          # list all tags
tapps-brain memory audit --last 50               # audit trail
tapps-brain maintenance health
tapps-brain maintenance consolidation-threshold-sweep --json   # read-only threshold tuning report
tapps-brain maintenance consolidation-merge-undo <consolidated-key>   # revert one auto-merge (audit-driven)
tapps-brain hive status
tapps-brain agent create my-agent --profile repo-brain
tapps-brain federation status
tapps-brain flywheel report --period-days 7
tapps-brain visual export -o brain-visual.json          # JSON snapshot for dashboard
tapps-brain visual capture --json brain-visual.json \   # headless PNG poster [visual] extra
    --output brain-visual.png --theme dark
tapps-brain export --format json --output backup.json
```

Typer CLI with multiple sub-apps (`store`, `memory`, `federation`, `maintenance`, `profile`, `hive`, `agent`, `feedback`, `diagnostics`, `flywheel`, `openclaw`, `visual`, …). Many commands support `--json` output.

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
store = MemoryStore(Path("."), profile_name="personal-assistant")
```

> **Deployed / multi-tenant brains:** profile selection happens via a registered `project_id` (env `TAPPS_BRAIN_PROJECT`, header `X-Tapps-Project`, or MCP `_meta.project_id`) — not by filesystem discovery. See [ADR-010](docs/planning/adr/ADR-010-multi-tenant-project-registration.md), [EPIC-069](docs/planning/epics/EPIC-069.md), and [docs/guides/mcp.md](docs/guides/mcp.md#project-identity-multi-tenant). Register with `tapps-brain project register <id> --profile ./profile.yaml`.

<details>
<summary><strong>Create a custom profile (in-process / seed document)</strong></summary>

Author the YAML locally, then either load it in-process or register it against a deployed brain:

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

Hive is a **feature of tapps-brain**, not a separate service. The `hive_*` tables live in the same Postgres as `private_memories` and `federation_*` (ADR-007), are served by the same `tapps-brain-http` container, and are reached through the same `/mcp/` + `/v1/*` API as private memory — writes with `agent_scope="hive"` (or `"domain"` / `"group:<n>"`) land in Hive namespaces; `agent_scope="private"` stays on the agent's row.

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Agent A  │  │ Agent B  │  │ Agent C  │     ── same /mcp/, same auth token ──
└────┬─────┘  └────┬─────┘  └────┬─────┘
     │ scope:      │ scope:      │ scope:
     │ domain      │ domain      │ hive
     ▼             ▼             ▼
┌────────────────────────────────────────────────────────┐
│            tapps-brain-http  (one container)           │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Postgres (one DB by default — ADR-007)          │  │
│  │   ├─ private_memories     (agent A, B, C rows)   │  │
│  │   ├─ hive_memories        (agent-a / agent-b /   │  │
│  │   │                        universal namespaces) │  │
│  │   └─ federation_*         (cross-project layer)  │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

For advanced deployments you can put Hive on a separate Postgres via `TAPPS_BRAIN_HIVE_DSN` — same API, different physical database. Default is one DSN, one DB.

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

62 modules, zero LLM dependencies, fully synchronous:

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
  │capture │ │ bm25 │ │promo │ │inject│ │Postgres │ │  hive  │
  │inject  │ │fusion│ │  gc  │ │sanit │ │pgvector │ │ agents │
  └────────┘ └──────┘ └──────┘ └──────┘ │tsvector │ └────────┘
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
| **Storage** | `store`, `postgres_private` | In-memory dict + PostgreSQL write-through (pgvector HNSW + tsvector GIN) |
| **Data** | `models`, `profile` | `MemoryEntry` (Pydantic v2), `MemoryProfile` with configurable layers |
| **Retrieval** | `retrieval`, `bm25`, `fusion` | Composite-scored ranked search, optional hybrid BM25+vector |
| **Lifecycle** | `decay`, `consolidation`, `auto_consolidation`, `gc`, `promotion` | Dual decay models, Jaccard+TF-IDF merging, archival GC, tier promotion |
| **Recall** | `recall`, `injection` | Orchestrator, capture pipeline, token-budgeted prompt injection |
| **Multi-Agent** | `postgres_hive`, `agent_brain`, `backends`, `agent_scope`, `memory_group` | Hive shared brain, namespace isolation, agent registry, propagation engine |
| **Integrations** | `reinforcement`, `extraction`, `session_index`, `doc_validation` | Boost, fact extraction, session search, doc scoring |
| **Safety** | `safety` | Prompt injection detection, content sanitization |
| **Federation** | `postgres_federation` | Cross-project pub/sub via PostgreSQL (ADR-007) |
| **Relations** | `relations`, `contradictions` | Entity/relation extraction, contradiction detection |
| **Extensions** | `embeddings`, `reranker`, `similarity` | pgvector HNSW semantic search, FlashRank local reranking, TF-IDF similarity |
| **Observability** | `metrics`, `audit`, `diagnostics`, `feedback`, `evaluation`, `flywheel`, `otel_exporter` | Counters, audit, quality scorecard, feedback store, eval/flywheel loop, optional OTel |
| **I/O** | `io`, `seeding` | JSON/Markdown import/export, project profile seeding |
| **Interfaces** | `cli`, `mcp_server` | Typer CLI (multi sub-app), FastMCP server (counts in `docs/generated/mcp-tools-manifest.json`) |
| **Infra** | `_protocols`, `_feature_flags` | Protocol interfaces, lazy optional dependency detection |

</details>

### Key design decisions

- **Synchronous core** — no async/await in the engine itself; `aio.AsyncMemoryStore` provides a thin `asyncio.to_thread` wrapper for async callers (EPIC-067)
- **Write-through cache** — every mutation updates both the in-memory dict and PostgreSQL atomically
- **Lazy decay** — dual-model decay evaluated on read, no background tasks or timers
- **Deterministic merging** — consolidation uses Jaccard + TF-IDF similarity thresholds, never LLM calls
- **Configurable limits** — max entries per profile (default 500, up to 1500+) with lowest-confidence eviction
- **Archive, don't delete** — GC moves stale entries to `archive.jsonl`, never destroys data
- **Profile-driven behavior** — layers, scoring, decay, promotion, GC, and Hive config all come from the active profile

---

## Development

```bash
uv sync --group dev

pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95

ruff check src/ tests/ && ruff format --check src/ tests/

mypy --strict src/tapps_brain/

pytest tests/benchmarks/ -v --benchmark-only

bash scripts/release-ready.sh
```

<details>
<summary><strong>Test structure</strong></summary>

```
tests/
├── unit/                35+ files — pure unit tests, no I/O
├── integration/         11+ files — real MemoryStore + Postgres
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
| [**Benchmarks**](docs/benchmarks/README.md) | **LoCoMo + LongMemEval** eval harness — methodology, reproducer CLI, cost envelope, and score tracking (D2 impact: STORY-SC01) |
| [TypeScript SDK](docs/guides/typescript-sdk.md) | `@tapps-brain/sdk` install, quick-start, API reference, and environment variables |
| [LangGraph adapter](docs/guides/langgraph-adapter.md) | `@tapps-brain/langgraph` LangGraph `BaseStore` drop-in — wiring, query translation, pagination notes |
| [Documentation index](docs/DOCUMENTATION_INDEX.md) | Categorized map of guides, engineering references, and planning epics |
| [**Environment variables**](docs/guides/postgres-dsn.md) | **Full env-var contract** — all variables, examples, required (prod/dev). Template: [`.env.example`](.env.example) |
| [Contributing](CONTRIBUTING.md) | Contributor setup (`uv`), tests, lint, types, and PR expectations |
| [Getting Started](docs/guides/getting-started.md) | Use-case map and quick example for each interface |
| [Profile Design Guide](docs/guides/profiles.md) | Custom profiles: layers, decay, scoring, promotion, Hive config |
| [Profile Catalog](docs/guides/profile-catalog.md) | All 6 built-in profiles with comparison tables |
| [**Fleet Topology**](docs/guides/fleet-topology.md) | **Deploying at scale** — N FastAPI containers + 1 brain sidecar, wire contract, deployment checklist, token lifecycle |
| [Hive Guide](docs/guides/hive.md) | Cross-agent memory sharing: namespaces, propagation, conflict resolution |
| [MCP Server Guide](docs/guides/mcp.md) | Client setup for Claude Code, Cursor, VS Code Copilot; full tool reference |
| [OpenClaw Guide](docs/guides/openclaw.md) | Install, configure, and test with OpenClaw |
| [OpenClaw runbook](docs/guides/openclaw-runbook.md) | Canonical PyPI + Git install, upgrade, verify, restart |
| [Auto-Recall Guide](docs/guides/auto-recall.md) | Recall orchestrator usage and integration patterns |
| [Publish checklist](scripts/publish-checklist.md) | PyPI pre-flight (includes release gate command) |
| [Federation Guide](docs/guides/federation.md) | Cross-project memory sharing setup |
| [Visual snapshot guide](docs/guides/visual-snapshot.md) | Export a `brain-visual.json` snapshot and explore the brain-visual dashboard |
| [Dashboard README](examples/brain-visual/README.md) | Live `/snapshot` polling, motion test checklist, brand notes |
| [Case Studies](docs/case-studies/README.md) | Production adopter case studies — template + submission guide |
| [Changelog](CHANGELOG.md) | Version history |

<details>
<summary><strong>Epic tracker (selected)</strong></summary>

| Epic | Title | Status |
|------|-------|--------|
| [EPIC-001](docs/planning/epics/EPIC-001.md)–[016](docs/planning/epics/EPIC-016.md) | Core platform (tests through Hive hardening) | Done |
| [EPIC-008](docs/planning/epics/EPIC-008.md) | MCP server | Done (tool/resource counts: [mcp-tools-manifest.json](docs/generated/mcp-tools-manifest.json); [MCP guide](docs/guides/mcp.md)) |
| [EPIC-029](docs/planning/epics/EPIC-029.md) | Feedback collection | Done |
| [EPIC-030](docs/planning/epics/EPIC-030.md) | Diagnostics & self-monitoring | Done |
| [EPIC-031](docs/planning/epics/EPIC-031.md) | Continuous improvement flywheel | Done |
| [EPIC-032](docs/planning/epics/EPIC-032.md) | OTel GenAI conventions | Planned |
| [EPIC-033](docs/planning/epics/EPIC-033.md) | OpenClaw plugin SDK alignment | Done |
| [EPIC-034](docs/planning/epics/EPIC-034.md)–[036](docs/planning/epics/EPIC-036.md) | Production QA, OpenClaw doc consistency, release gate | Done |

See [`docs/planning/STATUS.md`](docs/planning/STATUS.md) and [`docs/planning/epics/`](docs/planning/epics/) for the full list (including code-review epics 017–025).

</details>

---

## Early Adopters

tapps-brain is looking for **production deployments to highlight**. If you're running tapps-brain in a real agent fleet — coding assistants, customer-support bots, multi-tenant SaaS, anything — we'd love to list you here and write up how you're using it.

What you get:

- Listed in this README and in the [memory-systems scorecard](docs/research/memory-systems-scorecard.md) (helping tapps-brain move D10 from 1 → 3)
- A case study published under `docs/case-studies/<your-project>.md` (we'll draft it, you review before publish)
- White-glove onboarding support if you're still setting up

**Contact:** open an issue titled "Adopter: \<your project>" or email `tapp.thornton@gmail.com`. See the [case studies guide](docs/case-studies/README.md) for what a case study covers.

---

## License

[MIT](LICENSE) &copy; 2025 TappsMCP Contributors

<!-- docsmcp:start:table-of-contents -->

<!-- docsmcp:start:table-of-contents -->
## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Development](#development)
- [License](#license)
<!-- docsmcp:end:table-of-contents -->

<!-- docsmcp:start:features -->
## Features

- Python project with modern packaging (pyproject.toml)
- Test suite included
- CI/CD with GitHub Actions
- Docker support
- Documentation included
- 107 modules with 634 public APIs
- CLI entry points: src/tapps_brain/cli, src/tapps_brain/mcp_server, src/tapps_brain/mcp_server/server.py, tapps-brain = tapps_brain.cli:app, tapps-brain-http = tapps_brain.http_adapter:main
- FastAPI web framework
- Pydantic data validation
- pytest testing framework
<!-- docsmcp:end:features -->

<!-- docsmcp:start:usage -->
## Usage

### `tapps-brain`

```bash
tapps-brain
```

### `tapps-brain-http`

```bash
tapps-brain-http
```
<!-- docsmcp:end:usage -->

<!-- docsmcp:start:api-reference -->
## API Reference

See the [API documentation](docs/api.md) for detailed reference.
<!-- docsmcp:end:api-reference -->

<!-- docsmcp:start:contributing -->
## Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -am 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request
<!-- docsmcp:end:contributing -->
