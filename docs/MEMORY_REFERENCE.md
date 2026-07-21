# tapps-brain Memory Reference

Code-aligned reference for **tapps-brain** memory APIs — tiers, scopes, MCP tools, health diagnostics, and environment variables. Consumer repos typically access memory via **tapps-mcp BrainBridge** (`uv run tapps-mcp memory`); see [CONSUMER-REPO-BRAIN-WIRING.md](operations/CONSUMER-REPO-BRAIN-WIRING.md).

Generated MCP inventory: [`docs/generated/mcp-tools-manifest.json`](generated/mcp-tools-manifest.json) (65 tools, 8 resources at v3.24.0).

## Memory tiers

Defined in `src/tapps_brain/models.py` (`MemoryTier`). Half-lives from `src/tapps_brain/decay.py`:

| Tier | Half-life | Use for |
|------|-----------|---------|
| `architectural` | 180 days | Stable system decisions, infra contracts |
| `pattern` | 60 days | Coding conventions, API shapes |
| `procedural` | 30 days | Workflows, runbooks |
| `context` | 14 days | Short-lived session facts |

## Memory scopes

| Scope | Visibility |
|-------|------------|
| `project` | All sessions in this project (default) |
| `branch` | Sessions on the current git branch |
| `session` | Current session only |

Cross-project sharing uses Federation (not a `scope=` value). See [`docs/guides/federation.md`](guides/federation.md).

## Agent-facing API (`AgentBrain`)

Simplified 5-method facade in `src/tapps_brain/agent_brain.py`:

| Method | Purpose |
|--------|---------|
| `remember()` | Save a memory entry |
| `recall()` | Search or fetch by key |
| `forget()` | Archive (not hard delete) |
| `learn_from_success()` | Record successful outcome |
| `learn_from_failure()` | Record failed outcome |

Configured via `TAPPS_BRAIN_*` environment variables or constructor args.

## Core MCP tools (19)

Profile-gated subset always useful for agents:

| Tool | Purpose |
|------|---------|
| `brain_remember` | Save memory |
| `brain_recall` | Ranked recall |
| `brain_forget` | Archive by key |
| `brain_learn_success` / `brain_learn_failure` | Outcome learning |
| `brain_status` | Agent identity + store stats |
| `memory_save` / `memory_get` / `memory_delete` | CRUD |
| `memory_search` / `memory_recall` | BM25 + hybrid search |
| `memory_reinforce` | Boost confidence / reset decay |
| `memory_list` / `memory_ingest` / `memory_capture` | List, bulk ingest, capture |
| `hive_status` / `hive_search` / `hive_propagate` | Hive cross-agent memory |
| `tapps_brain_health` | Native health report |

Full tool list (operator, KG, maintenance, diagnostics): see manifest JSON above.

## Retrieval scoring

Composite score in `src/tapps_brain/retrieval.py`:

- Relevance 40%, confidence 30%, recency 15%, frequency 15% (default `repo-brain` profile; profile-tunable)
- Hybrid path: pgvector HNSW + tsvector fused via RRF (`fusion.py`)
- Pure-Python BM25 overlay in `bm25.py`

## Key environment variables

| Variable | Purpose |
|----------|---------|
| `TAPPS_BRAIN_DATABASE_URL` | **Required** — Postgres DSN for private memory (+ default Hive/Federation) |
| `TAPPS_BRAIN_HIVE_DSN` | Optional Hive-only Postgres override |
| `TAPPS_BRAIN_FEDERATION_DSN` | Optional Federation-only Postgres override |
| `TAPPS_BRAIN_AGENT_ID` | Agent identity string |
| `TAPPS_BRAIN_PROJECT_DIR` | Project root path |
| `TAPPS_BRAIN_GROUPS` | CSV Hive group memberships |
| `TAPPS_BRAIN_EXPERT_DOMAINS` | CSV expert domains for auto-publish |
| `TAPPS_BRAIN_AUTO_MIGRATE` | `1` = apply private migrations at `MemoryStore` startup (not recommended multi-host) |
| `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | Auto-run Hive schema migrations |
| `TAPPS_BRAIN_AUTH_TOKEN` | Bearer token for HTTP/MCP surfaces |

Full contract: [`docs/guides/postgres-dsn.md`](guides/postgres-dsn.md).

## Brain health diagnostics

Native health via `tapps_brain_health` MCP tool or `GET /healthz`. Implementation: `src/tapps_brain/health_check.py`.

### `StoreHealth` fields (operators)

| Field | Meaning |
|-------|---------|
| `status` | `ok` / `warn` / `error` |
| `entries` / `max_entries` | Row count vs profile cap (default 5000) |
| `schema_version` / `last_migration_version` | Applied private migration (max **024**) |
| `retrieval_effective_mode` | `bm25_only`, `hybrid_pgvector_hnsw`, etc. |
| `embedding_provider_healthy` | `sentence-transformers` importable |
| `pool_saturation` | Connection pool pressure (0.0–1.0) |

### Consumer-repo bridge health (`brain_bridge_health`)

When using tapps-mcp, `tapps_session_start()` returns `data.brain_bridge_health`:

| Field | Meaning |
|-------|---------|
| `enabled` | Bridge configured |
| `ok` | Reachable + native health passed |
| `dsn_reachable` | HTTP probe succeeded |
| `native_health_ok` | Brain `health` tool passed |
| `errors` / `warnings` | Actionable failure strings |

### Troubleshooting matrix

| Symptom | Fix |
|---------|-----|
| `brain_auth_failed` | Set `TAPPS_BRAIN_AUTH_TOKEN` in `.env`; restart MCP host; map to `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN` for CLI |
| `ValueError` on `MemoryStore()` | Set `TAPPS_BRAIN_DATABASE_URL` — no SQLite fallback (ADR-007) |
| `403` / profile gate | Raise `TAPPS_BRAIN_PROFILE` to `full` or tool's `suggested_profile` |
| Semantic recall degrades | Check `embedding_provider_healthy`; install `sentence-transformers` |
| Migration errors | Run `make brain-migrate` or migrate sidecar before starting http container |
| Project not found | `tapps-brain project register <slug>` on brain container |

CLI probe: `tapps-mcp doctor` (consumer repo) or `make brain-healthcheck` (this repo).

## Related docs

- [Memory import/export format matrix](guides/memory-import-export.md) — native JSON, MIF, relay, MEMORY.md, pg_dump
- [MCP client setup](guides/mcp-client-repo-setup.md) — direct brain HTTP MCP wiring
- [Hive guide](guides/hive.md) — cross-agent shared memory
- [Data stores and schema](engineering/data-stores-and-schema.md) — Postgres tables and migrations
- [Consumer repo wiring](operations/CONSUMER-REPO-BRAIN-WIRING.md) — tapps-mcp bridge checklist
