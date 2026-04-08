# Agent integration guide (MCP, OpenClaw, custom clients)

This page is the **operator contract** for AI agents using tapps-brain: how to write memory, how recall behaves when nothing returns, and where to read versions.

## AgentBrain (EPIC-057) — simplified Python API

The `AgentBrain` class is the recommended entry point for Python-based agents.
It wraps `MemoryStore` and `HiveBackend` creation, env-var resolution, and
lifecycle management into a single facade.

### Quick start

```python
from tapps_brain import AgentBrain

with AgentBrain(agent_id="frontend-dev", project_dir="/app") as brain:
    brain.remember("Use Tailwind for styling", tier="architectural")
    results = brain.recall("how to style components?")
    brain.learn_from_success("Styled the sidebar component")
```

### Configuration

| Parameter | Env var | Description |
|-----------|---------|-------------|
| `agent_id` | `TAPPS_BRAIN_AGENT_ID` | Agent identity for storage isolation |
| `project_dir` | `TAPPS_BRAIN_PROJECT_DIR` | Project root (defaults to cwd) |
| `groups` | `TAPPS_BRAIN_GROUPS` | Comma-separated group memberships |
| `expert_domains` | `TAPPS_BRAIN_EXPERT_DOMAINS` | Comma-separated expert domains |
| `hive_dsn` | `TAPPS_BRAIN_HIVE_DSN` | Hive backend DSN (SQLite path or `postgres://...`) |
| `encryption_key` | — | Encryption key for SQLCipher |

### Declaring groups and expert domains

```python
brain = AgentBrain(
    agent_id="css-specialist",
    project_dir="/app",
    groups=["dev-pipeline", "frontend"],
    expert_domains=["css", "tailwind"],
)
```

Or via environment variables:

```bash
export TAPPS_BRAIN_GROUPS="dev-pipeline,frontend"
export TAPPS_BRAIN_EXPERT_DOMAINS="css,tailwind"
```

### Configuring Hive DSN

For local-only mode (no shared storage), omit the DSN — a default SQLite
backend is used automatically.

For Postgres-backed Hive:

```python
brain = AgentBrain(
    agent_id="planner",
    project_dir="/app",
    hive_dsn="postgres://user:pass@host:5432/tapps_hive",
)
```

### Testing with local-only mode

In tests, pass `project_dir=tmp_path` (from pytest) and no `hive_dsn`:

```python
def test_my_agent(tmp_path):
    with AgentBrain(agent_id="test", project_dir=tmp_path) as brain:
        brain.remember("test fact")
        results = brain.recall("test")
        assert len(results) >= 1
```

## Versions and profile

| Signal | Where |
|--------|--------|
| **PyPI / package version** | `importlib.metadata.version("tapps-brain")`, CLI `tapps-brain --version`, or `StoreHealthReport.package_version` from `maintenance health` / `memory://stats` / `memory://health` |
| **SQLite schema** | `StoreHealthReport.schema_version`, `memory://stats` |
| **Active profile** | `StoreHealthReport.profile_name`, MCP `profile_info`, resource `memory://agent-contract` |
| **Profile seed recipe label** | `StoreHealthReport.profile_seed_version` (when `profile.seeding.seed_version` is set): `maintenance health`, `memory://stats`, `memory://health`, native `run_health_check` → `store.profile_seed_version` |

Always pin the **package version** in your repo’s `AGENTS.md` (or equivalent) so agents do not follow stale instructions.

## Writing memory

| Path | Command / tool |
|------|----------------|
| **MCP** | `memory_save` — primary path for assistants |
| **CLI** | `tapps-brain memory save KEY "value" [--tier …] [--tag …] [--group …]` — same semantics as MCP |
| **Bulk file** | `tapps-brain import data.json` — array of entries |
| **Python** | `MemoryStore.save(...)` |

There is **no** legacy `memory save` subcommand; use **`memory save`** (Typer sub-app `memory`).

## Reading / recall

| Path | Use when |
|------|-----------|
| `memory_search` | Full-text search with optional tier/scope/group filters |
| `memory_recall` | Ranked, injection-oriented bundle (`memory_section` + `memories`) |
| `memory_list` / `memory_get` | Browse or fetch by key |

### Empty `memory_recall`

When `memory_count` is `0`, check **`recall_diagnostics`** in the JSON response:

| `empty_reason` | Meaning |
|----------------|---------|
| `engagement_low` | Injection disabled for low engagement (orchestrator / client config) |
| `search_failed` | Retriever raised; check server logs |
| `store_empty` | No entries in the visible store |
| `group_empty` | No entries in the requested `group` (project-local `memory_group`) |
| `no_ranked_matches` | Store has rows but retriever returned nothing for this query |
| `below_score_threshold` | Candidates existed but all below the composite score cutoff |
| `rag_safety_blocked` | Candidates existed but values failed RAG safety checks |
| `post_filter_excluded` | Local/Hive results removed by orchestrator scope/tier/branch/dedupe filters |

Fields **`retriever_hits`** and **`visible_entries`** add context (see `RecallDiagnostics` in `models.py`).

## Tiers vs profile layers

- **Canonical enum tiers** (`architectural`, `pattern`, `procedural`, `context`, …) are always valid for decay and storage.
- **Profile layer names** (e.g. `identity`, `long-term`, `short-term` on `personal-assistant`) are also valid **when that profile is active**.
- Saves normalize aliases via `tier_normalize` (e.g. `long-term` → `architectural` where applicable).

See [Memory scopes](memory-scopes.md) and [Profile catalog](profile-catalog.md).

## Machine-readable surfaces

| Artifact | Purpose |
|----------|---------|
| `memory://agent-contract` | One JSON blob: versions, profile layers, canonical tiers, empty-reason codes, doc links |
| `docs/generated/mcp-tools-manifest.json` | `tool_count` / `resource_count`, tool names + resource URIs + short descriptions (regenerate: `python scripts/generate_mcp_tool_manifest.py`) |

## Related docs

- [MCP server](mcp.md) — setup and transport
- [OpenClaw](openclaw.md) — plugin and hooks
- [Getting started](getting-started.md)
