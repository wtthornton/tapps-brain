# Memory import / export format matrix

**Status:** added 2026-07-20 ([TAP-5027](https://linear.app/tappscodingagents/issue/TAP-5027) / [TAP-5029](https://linear.app/tappscodingagents/issue/TAP-5029)).

Operators and agents have several ways to move memories in and out of tapps-brain. Use this matrix to pick the right surface — do not invent ad-hoc dumps.

## Format matrix

| Format | Role | Backup? | Round-trip? | Primary API |
|--------|------|---------|-------------|-------------|
| **Native JSON** (`tapps-memory` 1.0) | Canonical portable dump of `MemoryEntry` records | Project-level yes | Yes (CLI / `io` / MCP) | `tapps-brain export`, `memory_export` |
| **Native JSONL** | Streaming export for large tenants | Project-level yes | Yes | `tapps-brain export -f jsonl` |
| **Lossless bundle** | Native envelope + `relations[]` + optional embeddings sidecar | Migration yes | Relations yes; embeddings when model ids match | `memory_export(include_relations=True)` |
| **MIF v2** | Vendor-neutral interchange | Interchange yes | Core fields + `extensions.tapps` | `export -f mif` / `export_format=mif` |
| **Frontmatter Markdown** | Human-editable Obsidian export | Soft backup | Yes via frontmatter import | `export -f markdown` + `import --mode frontmatter` |
| **MEMORY.md headings** | Ingest-only OpenClaw / vault migration | **No** (ingest only) | Keys are slugified — not lossless | `import_memory_md` / `--mode memory-md` |
| **Relay v1.0** | Cross-node agent handoff | No | Relay semantics only | `tapps_brain_relay_export` / `relay import` |
| **Managed Agents / OKF** | One-shot folder layout for offline agents | **No** | Read-only snapshot | [`brain_export`](brain-export.md) |
| **Mem0 / Letta .af** | Optional inbound adapters (preserve) | No | Inbound only | `import --format mem0\|letta-af` |
| **`pg_dump`** | Ops backup of record for Postgres | **Yes — authoritative** | Full DB | Operational runbooks |

## Defaults and safety

- **Import mode is preserve** — entries are written as provided. There is no LLM re-derive step on import.
- **Native envelope fields:** `format=tapps-memory`, `format_version=1.0`, `memories[]`.
- **Backward compatible:** bare `MemoryEntry` arrays still import.
- **Import size limit:** default 500 for MCP payloads; CLI defaults to 50 000. Override with `--max-entries` or `TAPPS_BRAIN_MAX_IMPORT_ENTRIES`. Exceeding the limit raises a clear error with counts (no silent truncate).
- **Embeddings:** optional sidecar / nested object keyed by memory `key`. Restored only when `embedding_model_id` matches the active provider; otherwise skipped with a warning.
- **Relations:** private SPO triples (`private_relations`) travel in the lossless bundle. First-class `kg_edges` remain in the Postgres ops plane (`pg_dump`).

## When to use what

1. **Backup / restore a project brain** → native JSON (or JSONL for large corpora). Add `--relations` for graph edges.
2. **Move memories to another 2026 memory system** → MIF v2.
3. **Feed Anthropic Managed Agents** → [`brain_export`](brain-export.md) (not a backup).
4. **Ingest a human MEMORY.md vault** → MEMORY.md heading mode (ingest-only).
5. **Operational disaster recovery** → `pg_dump` of the Postgres volume (source of truth per ADR-007).
6. **Migrate from Mem0 or Letta** → optional inbound adapters; then re-export as native or MIF.

## Related

- [`docs/guides/brain-export.md`](brain-export.md) — Managed Agents layout
- [`docs/guides/memory-relay.md`](memory-relay.md) — relay handoff
- [`docs/MEMORY_REFERENCE.md`](../MEMORY_REFERENCE.md) — tiers, scopes, MCP tools
- CLI: `tapps-brain export --help`, `tapps-brain import --help`
