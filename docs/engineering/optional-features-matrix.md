# Optional Features and Runtime Toggle Matrix

This matrix documents behavior changes from extras, feature checks, and profile-driven toggles.

## Dependency-driven optional behavior

| Area | Dependency / Extra | Enabled path | Fallback behavior |
|---|---|---|---|
| MCP server | `mcp` extra | MCP runtime in `mcp_server.py` | Startup error with install hint |
| Vector embeddings | `vector` extra (`sentence-transformers`) | Hybrid retrieval and embedding writes | Falls back to non-vector retrieval |
| sqlite-vec | `vector` extra (`sqlite-vec`) | `memory_vec` ANN path (per-agent local store) | Silent no-op; retrieval falls back |
| Reranker | `reranker` extra (`flashrank`) | Local cross-encoder re-ranking in injection pipeline | No-op reranker path |
| SQLCipher | `encryption` extra (`pysqlcipher3`) | Encrypted local SQLite connections | Plain sqlite when no key set; error if key set and dependency missing |
| OTel exporter | `otel` extra | exporter creation path | exporter disabled (`None`) |
| PostgreSQL Hive/Federation | `psycopg[binary]` + `psycopg_pool` (lazy, no extra) | `PostgresHiveBackend` / `PostgresFederationBackend` via `create_hive_backend("postgres://...")` | Falls back to SQLite (`HiveStore` / `FederatedStore`) when no DSN set |

## Profile and config toggles

| Toggle | Location | Effect |
|---|---|---|
| `limits.max_entries` | profile | Enforces local store cap (see [data-stores-and-schema](data-stores-and-schema.md#entry-cap-and-eviction-runtime)) |
| `seeding.seed_version` | profile | Optional label in `seed_from_profile` / `reseed_from_profile` summaries (`profile_seed_version`); also `StoreHealthReport.profile_seed_version`, CLI `maintenance health`, native `run_health_check.store.profile_seed_version`, MCP `memory://stats` |
| `hive.auto_propagate_tiers` | profile | Promotes matching private tiers to domain propagation |
| `hive.private_tiers` | profile | Forces matching tiers to private (no Hive propagation) |
| `hive.conflict_policy` | profile | Controls namespace write conflict behavior |
| `hive.recall_weight` | profile | Weights Hive results in merged recall |
| `hive.groups` | profile | Declarative group membership for this agent (EPIC-056) |
| `hive.expert_domains` | profile | Expert domains — auto-publish `architectural`/`pattern` tier saves to Hive (EPIC-056) |

## Interface-level toggles

| Surface | Toggle | Current default behavior |
|---|---|---|
| CLI | Store helper | Attaches configured Hive backend by default (Postgres if `TAPPS_BRAIN_HIVE_DSN` set, else SQLite) |
| CLI | `--agent-id` | Per-agent storage isolation (EPIC-053) |
| MCP | `--enable-hive / --no-enable-hive` | Enabled by default (`--enable-hive`) |
| MCP | `--agent-id` | Per-agent storage isolation, passed through to `MemoryStore` |
| Env | `TAPPS_BRAIN_HIVE_DSN` | Postgres DSN for shared Hive backend |
| Env | `TAPPS_BRAIN_FEDERATION_DSN` | Postgres DSN for Federation backend |
| Env | `TAPPS_BRAIN_AGENT_ID` | Agent identity (alternative to `--agent-id`) |
| Env | `TAPPS_BRAIN_GROUPS` | CSV group memberships |
| Env | `TAPPS_BRAIN_EXPERT_DOMAINS` | CSV expert domains |
| Env | `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | Auto-run Postgres schema migrations on startup |
| Env | `TAPPS_BRAIN_HIVE_POOL_MIN` / `_MAX` | Postgres connection pool sizing (default 2/10) |

## Health / operator surfaces (GitHub #63)

| Surface | What to read |
|---------|----------------|
| CLI | `tapps-brain diagnostics health` — includes **sqlite-vec** lines and **Retrieval:** (`retrieval_effective_mode` + summary) |
| MCP | `tapps_brain_health` — same fields in JSON under `store` |
| Guide | Optional stack semantics: this matrix; retrieval wording in `health_check.py` (`_retrieval_health_from_store`) |

## Important semantics to document clearly

- Hive behavior is additive, but interface defaults can still attach Hive automatically.
- Federation is explicit sync/publish, not automatic background replication.
- Federation `hub_path` in `federation.yaml` is honored by `FederatedStore()` when non-empty (see `federated_hub_db_path()` in `federation.py`).
- **Hive vs federation** (when to use which): `docs/guides/hive-vs-federation.md` (GitHub **#64**).
- **Backend selection** is by DSN string — `postgres://` → Postgres, path/None → SQLite. Callers never import a concrete backend class. See `backends.py` factory functions.
- **AgentBrain** (`agent_brain.py`) is the recommended entry point for agents — handles all backend wiring internally based on env vars.
