# Hive Guide: Cross-Agent Memory Sharing

> **v3 (current):** The Hive backend is **PostgreSQL-only** (ADR-007). Set
> `TAPPS_BRAIN_HIVE_DSN=postgres://…` to activate it. SQLite Hive support
> was removed in v3. Sections below that still reference `hive.db` or
> `HiveStore()` describe v2 behaviour; the underlying concepts (namespaces,
> propagation, conflict resolution) are unchanged. See
> [Hive Deployment Guide](hive-deployment.md) and
> [postgres-dsn.md](postgres-dsn.md) for v3 configuration.

The Hive is tapps-brain's multi-agent shared brain. It enables agents to share knowledge through a central PostgreSQL store with namespace isolation, conflict resolution, and configurable propagation.

> **Hive vs. Federation**: Federation shares memories across **projects**. The Hive shares memories across **agents** within or across projects. They solve different problems and can be used together. **Decision guide:** [`hive-vs-federation.md`](hive-vs-federation.md).

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
- [Scope Resolution](#scope-resolution)
- [Backup & Restore](#backup-restore)
- [Quotas](#quotas)
- [Propagation Denial Logging](#propagation-denial-logging)
- [Change Notification](#change-notification)

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

### Hive group scope (`agent_scope: "group:<name>"`) — GitHub #52

Use **`group:<name>`** (e.g. `group:team-alpha`) to write to a **Hive namespace** named `<name>`, shared only with agents **registered as members** of that Hive group (`HiveStore.create_group` / `add_group_member`). This is **cross-agent** sharing like `domain` / `hive`, but partitioned by explicit membership—not by profile alone.

- **Not** the same as project-local `memory_group` / CLI `--group` (GitHub #49): those label rows inside the **project** SQLite DB; `group:<name>` targets the **Hive** store and requires **Hive** group membership.
- **Propagation:** If the saving agent is not a member of `<name>`, the write stays local (no Hive row); a warning is logged (`hive.propagate.group_denied`).
- **Recall:** Hive-aware recall searches **universal**, the agent’s **profile (domain) namespace**, and **every Hive group namespace** the agent belongs to, then merges with local results (same weighting as other Hive hits).

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
| `group:<name>` | Hive namespace `<name>` | Small-team or feature cohort facts; requires Hive group membership |

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

## Scope Resolution

When a query arrives, the recall orchestrator resolves which Hive namespaces to search. The resolution order determines where results come from:

```
Query arrives
  │
  ▼
┌─────────────────────────────────────────────────────┐
│  1. Check agent_scope on incoming save / propagate  │
│     ┌─────────────┬─────────────────────────────┐   │
│     │  "private"   → stays local, no Hive write │   │
│     │  "domain"    → agent's profile namespace  │   │
│     │  "hive"      → "universal" namespace      │   │
│     │  "group:<n>" → Hive namespace <n>         │   │
│     │               (requires group membership) │   │
│     └─────────────┴─────────────────────────────┘   │
│                                                     │
│  Profile overrides (applied before scope routing):  │
│     private_tiers  → force scope to "private"       │
│     auto_propagate_tiers → upgrade "private"→"domain"│
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│  2. Recall: build namespace list for Hive search    │
│     ┌───────────────────────────────────────────┐   │
│     │  a) "universal"            (always)       │   │
│     │  b) agent's profile name   (domain ns)    │   │
│     │  c) each Hive group the agent belongs to  │   │
│     └───────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│  3. Hive lookup order                               │
│     Search all resolved namespaces in one query     │
│     → Deduplicate (local wins on key collision)     │
│     → Apply hive recall_weight to Hive scores       │
│     → Merge with local results, sort by score desc  │
└─────────────────────────────────────────────────────┘
```

`normalize_agent_scope()` (in `agent_scope.py`) validates and canonicalizes the scope string. The `group:` prefix is case-insensitive (`GROUP:FOO` becomes `group:FOO`); the group name itself preserves case. Empty or whitespace-only group names are rejected. Group names longer than 64 characters or containing ASCII control characters are rejected (same rules as `memory_group`).

---

## Backup & Restore

### Database location

The Hive database is stored at `~/.tapps-brain/hive/hive.db`. Related files:

| File | Description |
|------|-------------|
| `~/.tapps-brain/hive/hive.db` | Main SQLite database |
| `~/.tapps-brain/hive/hive.db-wal` | Write-Ahead Log (WAL) file; contains uncommitted transactions |
| `~/.tapps-brain/hive/hive.db-shm` | Shared memory file for WAL mode |
| `~/.tapps-brain/hive/agents.yaml` | Agent registry (YAML) |
| `~/.tapps-brain/hive/.hive_write_notify` | Sidecar file for file-based write watchers |

### Backup procedure

1. **Stop all agents** that write to the Hive (or accept brief inconsistency for read-only backups).

2. **Copy the database and WAL together** -- both files must be copied atomically to avoid corruption:

```bash
# Safe backup using sqlite3 .backup command (handles WAL correctly)
sqlite3 ~/.tapps-brain/hive/hive.db ".backup '/path/to/backup/hive-$(date +%Y%m%d).db'"

# Also back up the agent registry
cp ~/.tapps-brain/hive/agents.yaml /path/to/backup/agents-$(date +%Y%m%d).yaml
```

3. **Alternative: filesystem copy** (only when no writer is active):

```bash
cp ~/.tapps-brain/hive/hive.db /path/to/backup/
cp ~/.tapps-brain/hive/hive.db-wal /path/to/backup/  # if it exists
```

### Restore procedure

1. Stop all agents.
2. Replace the database file (and WAL if present):

```bash
cp /path/to/backup/hive-20260404.db ~/.tapps-brain/hive/hive.db
rm -f ~/.tapps-brain/hive/hive.db-wal ~/.tapps-brain/hive/hive.db-shm
cp /path/to/backup/agents-20260404.yaml ~/.tapps-brain/hive/agents.yaml
```

3. Restart agents. The Hive schema migration runs on connect and is idempotent.

### Encrypted Hive databases

SQLCipher at-rest encryption is no longer supported — SQLite was retired in ADR-007. For Postgres at-rest encryption, see the [pg_tde runbook](postgres-tde.md) and the [Postgres backup/restore runbook](postgres-backup.md) *(guide removed — SQLite retired in ADR-007)*.

---

## Quotas

### Current state

There are currently **no server-wide or per-namespace quotas** enforced on Hive writes. Any registered agent can write an unlimited number of entries to any namespace it has access to.

Profile-level `limits.max_entries` applies to the **local** project store only, not to the Hive.

### Planned improvements

- **Per-namespace entry cap**: limit the number of entries per namespace to prevent a single agent from flooding `universal`.
- **Rate limiting**: throttle write frequency per agent to prevent hot loops from overwhelming the shared store.
- **Namespace size alerts**: `hive_status` will report namespaces approaching configurable thresholds.

Until quotas are implemented, use `hive_status` to monitor namespace growth and review `private_tiers` configuration to limit what gets propagated.

---

## Propagation Denial Logging

When a propagation is denied, structured log events are emitted with the reason:

| Log event | Reason | Description |
|-----------|--------|-------------|
| `hive.propagate.group_denied` | `not_a_member` | Agent tried to write to a `group:<name>` namespace but is not a registered member of that group. |
| `hive.conflict.source_authority_rejected` | source mismatch | Under `source_authority` conflict policy, a non-original author tried to update an existing entry. |
| `hive.conflict.confidence_max_kept_existing` | lower confidence | Under `confidence_max` policy, the new write had lower confidence than the existing entry. |

These events are emitted at `warning` (denials) or `info` (kept-existing) level via structlog. To collect denial metrics, configure your observability pipeline to count occurrences of these event names. The `details` fields include `agent_id`, `key`, `namespace`, and `reason` for filtering and alerting.

---

## Change Notification

The Hive supports lightweight change notification via a **monotonic revision counter** stored in the `hive_write_notify` table. Every successful write increments the revision.

### Polling with `hive_write_revision`

Query the current revision without blocking:

```python
state = hive.get_write_notify_state()
# {"revision": 42, "updated_at": "2026-04-04T12:00:00+00:00"}
```

MCP tool: `hive_write_revision` returns the same dict.

### Blocking wait with `hive_wait_write`

Block until a new write occurs or a timeout elapses:

```python
result = hive.wait_for_write_notify(
    since_revision=42,
    timeout_sec=30.0,
    poll_interval_sec=0.25,   # default
)
# result: {"revision": 43, "updated_at": "...", "changed": True, "timed_out": False}
```

MCP tool: `hive_wait_write` exposes the same interface.

### Sidecar file

Each write also updates `~/.tapps-brain/hive/.hive_write_notify` (plain text: `revision\nupdated_at\n`) so external tools can watch for changes via filesystem events without opening the SQLite database.

### Backoff policy

When using `hive_wait_write` in a client loop:

1. **Start with the default poll interval** (`poll_interval_sec=0.25`).
2. **Use reasonable timeouts** -- 10-60 seconds per call. The MCP tool defaults to 30s.
3. **On timeout (no change), increase the next call's timeout** -- double it up to a cap (e.g., 60s) to avoid hot-spinning when the Hive is idle.
4. **On change, reset to the base timeout** -- the Hive is active, so poll frequently.
5. **Avoid sub-100ms poll intervals** -- the SQLite read is cheap but filesystem thrashing on the WAL is not.

```
timeout = 10s  (initial)
loop:
    result = hive_wait_write(since_revision=last_rev, timeout_sec=timeout)
    if result.changed:
        process(result)
        last_rev = result.revision
        timeout = 10s          # reset on activity
    else:
        timeout = min(timeout * 2, 60s)  # exponential backoff on idle
```

### Future: filesystem watch (inotify / ReadDirectoryChangesW)

For lower-latency notification than polling, a future release may add optional filesystem-watch support:

- **Linux**: `inotify` on `~/.tapps-brain/hive/.hive_write_notify` (the sidecar file is designed for this).
- **macOS**: `FSEvents` or `kqueue` on the same sidecar file.
- **Windows**: `ReadDirectoryChangesW` on the `~/.tapps-brain/hive/` directory.

This would be an **optional sidecar process**, not a change to the core library. The sidecar file format is stable and designed to be watched. The polling API will remain the primary interface for cross-platform compatibility.

---

## Further Reading

- [Profile Design Guide](profiles.md) — Designing custom profiles with Hive config
- [Profile Catalog](profile-catalog.md) — Built-in profiles and their Hive settings
- [Federation Guide](federation.md) — Cross-project sharing (complements Hive)
- [Scope Audit](scope-audit.md) — `agent_scope` / group / hive namespace matrix and code checklist (EPIC-063)
