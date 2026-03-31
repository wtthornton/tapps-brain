# Agent integration guide (MCP, OpenClaw, custom clients)

This page is the **operator contract** for AI agents using tapps-brain: how to write memory, how recall behaves when nothing returns, and where to read versions.

## Versions and profile

| Signal | Where |
|--------|--------|
| **PyPI / package version** | `importlib.metadata.version("tapps-brain")`, CLI `tapps-brain --version`, or `StoreHealthReport.package_version` from `maintenance health` / `memory://stats` / `memory://health` |
| **SQLite schema** | `StoreHealthReport.schema_version`, `memory://stats` |
| **Active profile** | `StoreHealthReport.profile_name`, MCP `profile_info`, resource `memory://agent-contract` |

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
