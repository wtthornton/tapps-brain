# Hive Guide: Cross-Agent Memory Sharing

The Hive is tapps-brain's multi-agent shared brain. It enables agents on the same machine to share knowledge through a central SQLite store with namespace isolation, conflict resolution, and configurable propagation.

> **Hive vs. Federation**: Federation shares memories across **projects** (`~/.tapps-brain/memory/federated.db`). The Hive shares memories across **agents** within or across projects (`~/.tapps-brain/hive/hive.db`). They solve different problems and can be used together.

## Table of Contents

- [Overview](#overview)
- [Who attaches `HiveStore`?](#who-attaches-hivestore)
- [Architecture](#architecture)
- [Namespaces](#namespaces)
- [Agent Registration](#agent-registration)
- [Propagation](#propagation)
- [Conflict Resolution](#conflict-resolution)
- [Search Across Agents](#search-across-agents)
- [Profile Integration](#profile-integration)
- [Architecture Patterns](#architecture-patterns)
- [MCP Tools](#mcp-tools)
- [Python API](#python-api)
- [Best Practices](#best-practices)

---

## Overview

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Agent A    │  │  Agent B    │  │  Agent C    │
│  (local     │  │  (local     │  │  (local     │
│   store)    │  │   store)    │  │   store)    │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       │   propagate    │   propagate    │   propagate
       │   (scope:      │   (scope:      │   (scope:
       │    domain/     │    domain/     │    domain/
       │    hive)       │    hive)       │    hive)
       ▼                ▼                ▼
┌─────────────────────────────────────────────────┐
│                   Hive Store                     │
│            ~/.tapps-brain/hive/hive.db           │
│                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │universal │ │ agent-a  │ │ agent-b          │ │
│  │namespace │ │namespace │ │ namespace        │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
└─────────────────────────────────────────────────┘
```

Key properties:
- **SQLite with WAL mode** — concurrent reads, single writer, FTS5 full-text search
- **Namespace isolation** — each agent writes to its own namespace; cross-namespace search available
- **Backward compatible core model** — local single-agent behavior remains valid. In practice, whether Hive is attached depends on interface/runtime configuration.
- **Thread-safe** — all operations are protected by `threading.Lock`

### Who attaches `HiveStore`?

| Interface | Default | Notes |
|-----------|---------|--------|
| **CLI** (`tapps-brain` commands) | **Attached** — `HiveStore()` is passed into `MemoryStore` | No `--no-hive` flag yet; use the library with `hive_store=None` if you need a Hive-free CLI-style workflow. |
| **MCP** (`tapps-brain-mcp`) | **Attached** — `enable_hive=True` by default | Pass `--no-enable-hive` to run without an attached Hive. |
| **Python library** | **Not attached** unless you pass `hive_store=` | e.g. `MemoryStore(path, hive_store=HiveStore())` to match CLI/MCP behavior. |

Profile `hive` settings (tiers, conflict policy, `recall_weight`) apply only when a `HiveStore` is attached — they do not turn Hive on or off by themselves.

---

## Architecture

### Storage

The Hive uses a single SQLite database at `~/.tapps-brain/hive/hive.db` with:
- **WAL mode** for concurrent read access
- **FTS5 virtual table** for full-text search across namespaces
- **Primary key**: `(namespace, key)` — the same key can exist in different namespaces without collision

### Schema

Each Hive entry stores:

| Field | Description |
|-------|-------------|
| `namespace` | Domain namespace (default: `universal`) |
| `key` | Memory key (unique within namespace) |
| `value` | Memory content |
| `tier` | Layer/tier name from the originating profile |
| `confidence` | Confidence score (0.0–1.0) |
| `source` | Source type (human, agent, inferred, system) |
| `source_agent` | Agent ID that created the entry |
| `tags` | JSON array of tags |
| `created_at` | ISO-8601 creation timestamp |
| `updated_at` | ISO-8601 last update timestamp |
| `valid_at` | Bi-temporal: when the fact became true |
| `invalid_at` | Bi-temporal: when the fact stopped being true |
| `superseded_by` | Key of the superseding entry |
| `memory_group` | Publisher's project-local partition label (optional, `null` means ungrouped) |

> **`memory_group` (GitHub #51):** When a publisher propagates an entry that carries a project-local `memory_group` label, that label is preserved verbatim in the Hive entry. Subscribers that implement partition semantics can read this field to restore group membership after a pull. A value of `null`/`None` means the entry was not assigned to any group.

---

## Namespaces

Namespaces provide isolation between agents while enabling controlled sharing.

### The `universal` namespace

Entries with `agent_scope: "hive"` are written to the `universal` namespace. All agents can read from it. Use for platform-wide knowledge that every agent needs:
- System architecture decisions
- Security policies
- Shared configuration

### Domain namespaces

Entries with `agent_scope: "domain"` are written to a namespace matching the agent's profile name. Only agents with the same profile share a namespace by default:
- An agent with `profile: "developer"` writes to namespace `developer`
- An agent with `profile: "qa"` writes to namespace `qa`
- Cross-namespace search is always available

### Private entries

Entries with `agent_scope: "private"` stay in the agent's local store and are never propagated to the Hive.

---

## Agent Registration

Agents register with the Hive via a YAML-backed registry at `~/.tapps-brain/hive/agents.yaml`.

### Registration fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique agent slug (e.g., `"qa-agent-1"`) |
| `name` | No | Human-readable name |
| `profile` | No | Memory profile name (default: `"repo-brain"`). Determines domain namespace. |
| `skills` | No | List of capabilities (e.g., `["code-review", "testing"]`) |
| `project_root` | No | Absolute path to the agent's project (for project-scoped agents) |

### Registering via MCP

```
tool: agent_register
args:
  agent_id: "qa-agent-1"
  profile: "thestudio"
  skills: "defect-detection,intent-validation"
```

### Registering via Python

```python
from tapps_brain.hive import AgentRegistry, AgentRegistration

registry = AgentRegistry()
registry.register(AgentRegistration(
    id="qa-agent-1",
    name="QA Agent",
    profile="thestudio",
    skills=["defect-detection", "intent-validation"],
    project_root="/path/to/project",
))

# List all registered agents
agents = registry.list_agents()

# Find agents with a specific profile
qa_agents = registry.agents_for_domain("thestudio")
```

---

## Propagation

The `PropagationEngine` routes memory entries to the Hive based on `agent_scope`:

### Scope routing

| `agent_scope` | Destination | When to use |
|---------------|-------------|-------------|
| `private` | Stays in local store | Agent-specific working memory, session context |
| `domain` | Agent's profile namespace | Domain knowledge shared with same-role agents |
| `hive` | `universal` namespace | Platform-wide facts all agents should know |

### Auto-propagation

The profile's `hive` config can automatically upgrade or downgrade propagation scope:

```yaml
hive:
  auto_propagate_tiers:
    - "platform-invariants"   # Always propagate, even if saved as private
    - "expert-knowledge"
  private_tiers:
    - "signals"               # Never propagate, even if saved as hive scope
    - "ephemeral"
```

**Resolution order**:
1. If the entry's tier is in `private_tiers` → scope forced to `private` (highest priority)
2. If the entry's tier is in `auto_propagate_tiers` and scope is `private` → scope upgraded to `domain`
3. Otherwise → original scope preserved

### Manual propagation

Propagate an existing local memory to the Hive:

```python
from tapps_brain.hive import HiveStore, PropagationEngine

hive = HiveStore()
result = PropagationEngine.propagate(
    key="auth-pattern",
    value="This project uses JWT with refresh rotation",
    agent_scope="hive",          # → universal namespace
    agent_id="developer-1",
    agent_profile="thestudio",
    tier="platform-invariants",
    confidence=0.9,
    source="human",
    tags=["auth", "security"],
    hive_store=hive,
)
hive.close()
```

### Batch push (CLI / MCP, GitHub #18)

From the project store, promote many entries in one go:

```bash
tapps-brain hive push --all --scope hive --project-dir .
tapps-brain hive push --tags shared,reviewed --scope domain --dry-run
tapps-brain hive push --keys my-key,other-key --scope hive
tapps-brain hive push-tagged shared --scope hive
```

Use **`--force`** to ignore profile `private_tiers` / `auto_propagate_tiers` so the chosen `domain` or `hive` scope always applies. **`--dry-run`** prints counts without writing to Hive.

---

## Conflict Resolution

When an agent writes a key that already exists in the same namespace, the conflict policy determines the outcome:

### Policies

| Policy | Behavior | Best for |
|--------|----------|----------|
| **`supersede`** | Invalidates the old entry (sets `invalid_at`, `superseded_by`) and creates a new versioned key. Full audit trail preserved. | Compliance, versioned knowledge, audit trails |
| **`source_authority`** | Rejects the write if the new agent doesn't match the existing entry's `source_agent`. Only the original author can update. | Domain-owned namespaces where one agent is authoritative |
| **`confidence_max`** | Keeps whichever version has higher confidence. Lower-confidence write is silently dropped. | Multi-agent convergence where truth should win |
| **`last_write_wins`** | Overwrites unconditionally. No conflict detection. | Simple systems, low contention, development/testing |

### Setting the policy

In the profile:
```yaml
hive:
  conflict_policy: "confidence_max"
```

Or per-write via the Python API:
```python
hive.save(
    key="expert-rating",
    value="Security reviewer: trusted tier",
    namespace="experts",
    source_agent="reputation-engine",
    conflict_policy=ConflictPolicy.confidence_max,
    confidence=0.85,
)
```

### 2026 best practice: explicit conflict resolution

Research in 2026 identifies multi-agent memory consistency as the "largest conceptual gap" in current frameworks. The key recommendation: **make versioning, visibility, and conflict-resolution rules explicit** so agents agree on what to read and when updates take effect. tapps-brain's conflict policies implement this directly.

---

## Search Across Agents

### Search all namespaces

```python
hive = HiveStore()
results = hive.search("authentication", min_confidence=0.3, limit=20)
```

### Search specific namespaces

```python
# Only search QA and universal namespaces
results = hive.search(
    "defect patterns",
    namespaces=["qa", "universal"],
    min_confidence=0.5,
)
```

### Hive-aware recall

When a `MemoryStore` is initialized with a `hive_store` parameter, recall automatically merges local and Hive results. The `recall_weight` in the profile config (default 0.8) controls how Hive results are weighted relative to local results.

---

## Profile Integration

The Hive config is part of the memory profile. See the [Profile Design Guide](profiles.md#hive-configuration) for full schema details.

### Key profile fields for Hive

```yaml
hive:
  auto_propagate_tiers: ["architectural", "pattern"]  # Current code defaults
  private_tiers: ["context"]                           # Current code defaults
  conflict_policy: "supersede"                         # Default conflict resolution
  recall_weight: 0.8                                   # Weight for Hive results in recall (0.0-1.0)
```

### How profile name maps to namespace

When an agent registers with `profile: "thestudio"`, its domain namespace becomes `thestudio`. When it saves a memory with `agent_scope: "domain"`, the entry goes to the `thestudio` namespace in the Hive.

---

## Architecture Patterns

### Pattern 1: Shared profile, namespace-per-agent

All agents use the same profile. Each agent has its own namespace based on its agent ID or role. Shared knowledge goes to `universal`.

```yaml
# Agent registry (agents.yaml)
agents:
  - id: "developer-1"
    profile: "thestudio"
    skills: ["implementation", "testing"]
  - id: "qa-1"
    profile: "thestudio"
    skills: ["defect-detection", "intent-validation"]
  - id: "router-1"
    profile: "thestudio"
    skills: ["expert-routing", "risk-assessment"]
```

**Pros**: Shared layer vocabulary, easy cross-agent search, simple configuration.
**Cons**: All agents use the same scoring weights and decay rates.

### Pattern 2: Per-role profiles with shared Hive

Each agent role gets a profile tuned to its needs. The Hive provides the shared layer.

```yaml
# developer uses recency-heavy scoring
# qa uses confidence-heavy scoring
# both propagate "platform-invariants" to universal namespace
```

**Pros**: Optimized scoring and decay per role.
**Cons**: More profiles to maintain, cross-agent consolidation harder with different tier names.

### Pattern 3: Base profile + role extensions

One base profile with role-specific overrides via `extends`. Best of both worlds.

```yaml
# thestudio-qa.yaml
profile:
  name: "thestudio-qa"
  extends: "thestudio"
  scoring:
    confidence: 0.40
    relevance: 0.25
    recency: 0.20
    frequency: 0.15
```

**Pros**: Shared vocabulary, role-specific tuning, minimal duplication.
**Cons**: Requires inheritance system understanding.

### Choosing a pattern

- **Start with Pattern 1** unless you have proven scoring/decay divergence between roles
- **Move to Pattern 3** when a specific role shows poor recall quality with shared weights
- **Use Pattern 2** only for truly independent agent systems that don't share a domain

---

## MCP Tools

Hive-related MCP tools include:

| Tool | Description |
|------|-------------|
| `hive_status` | Returns namespaces, entry counts, and registered agents |
| `hive_search` | Search the Hive with optional namespace filter |
| `hive_propagate` | Manually propagate a local memory to the Hive (`force`, `dry_run` optional) |
| `hive_push` | Batch-promote local memories (`push_all`, `tags`, `tier`, `keys`, `dry_run`, `force`) |
| `agent_register` | Register an agent in the Hive registry |
| `profile_info` | Return the active profile's layers and scoring config |
| `profile_switch` | Switch to a different built-in profile |

### Example: hive_status

```json
{
  "namespaces": {
    "universal": 12,
    "developer": 45,
    "qa": 23
  },
  "agents": [
    {"id": "developer-1", "profile": "thestudio", "skills": ["implementation"]},
    {"id": "qa-1", "profile": "thestudio", "skills": ["defect-detection"]}
  ]
}
```

---

## Python API

### HiveStore

```python
from tapps_brain.hive import HiveStore

hive = HiveStore()  # uses ~/.tapps-brain/hive/hive.db

# Save
hive.save(key="fact-1", value="...", namespace="universal", source_agent="agent-1")

# Get
entry = hive.get("fact-1", namespace="universal")

# Search
results = hive.search("query", namespaces=["universal", "developer"])

# List namespaces
ns = hive.list_namespaces()  # ["developer", "qa", "universal"]

hive.close()
```

### PropagationEngine

```python
from tapps_brain.hive import PropagationEngine, HiveStore

hive = HiveStore()
result = PropagationEngine.propagate(
    key="key",
    value="value",
    agent_scope="domain",       # or "hive" or "private"
    agent_id="my-agent",
    agent_profile="my-profile", # determines namespace for "domain" scope
    tier="pattern",
    confidence=0.8,
    source="agent",
    tags=["tag1"],
    hive_store=hive,
    auto_propagate_tiers=["architectural"],
    private_tiers=["context"],
)
# result is None if scope resolved to "private"
```

### AgentRegistry

```python
from tapps_brain.hive import AgentRegistry, AgentRegistration

registry = AgentRegistry()

# Register
registry.register(AgentRegistration(id="my-agent", profile="my-profile"))

# List
agents = registry.list_agents()

# Find by domain
domain_agents = registry.agents_for_domain("my-profile")

# Unregister
registry.unregister("my-agent")
```

---

## Best Practices

### 1. Always set private_tiers

Every profile with Hive enabled should explicitly list which tiers are private. Without this, all tiers can propagate, flooding the Hive with ephemeral data.

### 2. Use agent_scope intentionally

Don't default everything to `"hive"`. Most memories should be `"private"` (local only) or `"domain"` (shared with same-role agents). Reserve `"hive"` for universal truths.

### 3. Register agents with meaningful skills

The `skills` field in agent registration enables future discovery (e.g., "find an agent that can do code-review"). Use specific, lowercase skill names.

### 4. Choose conflict policy based on write patterns

- Single writer per namespace → `source_authority`
- Multiple writers, truth matters → `confidence_max`
- Multiple writers, recency matters → `last_write_wins`
- Need audit trail → `supersede`

### 5. Monitor namespace growth

Use `hive_status` to check entry counts per namespace. If a namespace grows much larger than others, the agent may be over-propagating.

### 6. Hive is not a replacement for the local store

The Hive is a sharing layer, not a primary store. Each agent should have its own `MemoryStore` for local operations. The Hive supplements recall with cross-agent knowledge.

---

## Further Reading

- [Profile Design Guide](profiles.md) — Designing custom profiles with Hive config
- [Profile Catalog](profile-catalog.md) — Built-in profiles and their Hive settings
- [Federation Guide](federation.md) — Cross-project sharing (complements Hive)
