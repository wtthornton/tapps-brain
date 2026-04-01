# Optional Features and Runtime Toggle Matrix

This matrix documents behavior changes from extras, feature checks, and profile-driven toggles.

## Dependency-driven optional behavior

| Area | Dependency / Extra | Enabled path | Fallback behavior |
|---|---|---|---|
| MCP server | `mcp` extra | MCP runtime in `mcp_server.py` | Startup error with install hint |
| Vector embeddings | `vector` extra (`sentence-transformers`) | Hybrid retrieval and embedding writes | Falls back to non-vector retrieval |
| sqlite-vec | `vector` extra (`sqlite-vec`) | `memory_vec` ANN path | Silent no-op; retrieval falls back |
| Reranker | `reranker` extra (`cohere`) | Re-ranking in injection pipeline | No-op reranker path |
| SQLCipher | `encryption` extra (`pysqlcipher3`) | Encrypted SQLite connections | Plain sqlite when no key set; error if key set and dependency missing |
| OTel exporter | `otel` extra | exporter creation path | exporter disabled (`None`) |

## Profile and config toggles

| Toggle | Location | Effect |
|---|---|---|
| `limits.max_entries` | profile | Enforces local store cap |
| `hive.auto_propagate_tiers` | profile | Promotes matching private tiers to domain propagation |
| `hive.private_tiers` | profile | Forces matching tiers to private (no Hive propagation) |
| `hive.conflict_policy` | profile | Controls namespace write conflict behavior |
| `hive.recall_weight` | profile | Weights Hive results in merged recall |

## Interface-level toggles

| Surface | Toggle | Current default behavior |
|---|---|---|
| CLI | Store helper | Attaches `HiveStore()` by default |
| MCP | `--enable-hive / --no-enable-hive` | Enabled by default (`--enable-hive`) |

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
