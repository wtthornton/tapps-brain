# Industry features and technologies (implementation map)

**Audience:** Architecture and product review — what capability areas we cover, which libraries/patterns we use, and where behavior lives in code.

**Improvement program (epics + stories):** [`docs/planning/epics/EPIC-042-feature-tech-index.md`](../planning/epics/EPIC-042-feature-tech-index.md) — maps each section here to **EPIC-042** … **EPIC-051** with research notes and implementation themes.

**Related:** [`optional-features-matrix.md`](optional-features-matrix.md), [`data-stores-and-schema.md`](data-stores-and-schema.md), [`call-flows.md`](call-flows.md), [`system-architecture.md`](system-architecture.md).

---

## 1. Retrieval and ranking (RAG-style memory)

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **Lexical / keyword search** | SQLite **FTS5** + in-process **Okapi BM25** | FTS for candidate generation / filtering paths; `bm25.py` implements BM25 with stop-word stripping and light normalization (pure Python, no IR server). |
| **Dense retrieval / semantic search** | **`sentence-transformers`** + **`numpy`** + **`sqlite-vec`** | Embeddings computed in `embeddings.py` (core dependency); vectors stored on entries and in sqlite-vec table (`memory_vec`). **Model card:** [`embedding-model-card.md`](../guides/embedding-model-card.md). |
| **Vector index in DB** | **`sqlite-vec`** (`vec0`, table `memory_vec`) | `persistence.py` / `sqlite_vec_index.py`; KNN path when extension + embeddings available; health reports `sqlite_vec_enabled` / row counts. Ops: [`sqlite-vec-operators.md`](../guides/sqlite-vec-operators.md). |
| **Hybrid search** | **Reciprocal Rank Fusion (RRF)** | `fusion.py` merges BM25-ranked and vector-ranked lists; **weighted RRF** via `hybrid_rrf_weights_for_query()` (GitHub #40) — deterministic query heuristics, no LLM. Per-channel recall depth and RRF *k* are optional under **`profile.hybrid_fusion`** (`HybridFusionConfig` in `profile.py`; YAML aliases `top_k_lexical` / `top_k_dense`). |
| **Composite ranking** | Weighted score blend | `retrieval.py`: relevance 40%, confidence 30%, recency 15%, frequency 15%; per-source trust multipliers after composite; profile can tune scoring where wired. |
| **Re-ranking (local cross-encoder)** | **FlashRank** (`[reranker]` extra) | `reranker.py`; used in injection pipeline when installed; falls back to noop. Runs entirely on-device, no API key needed. |
| **Token-budgeted context** | Fixed caps + estimates | `injection.py`: `InjectionConfig.injection_max_tokens` (default 2000), per-tier max inject counts, `_MIN_SCORE` floor before inject. |
| **Stale / decayed relevance** | **Exponential decay** + optional **FSRS-like fields** | `decay.py` lazy decay on read; `models.py` carries `stability` / `difficulty`; hybrid model + recall vs reinforce updates in [`memory-decay-and-fsrs.md`](../guides/memory-decay-and-fsrs.md). **Checklist 10.2:** lazy decay + operator GC — no mandatory wall-clock TTL jobs in core; [`ADR-002`](../planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md). |

**Explicit boundaries:** Core retrieval does **not** call an LLM to score documents. “Relevance” is BM25 ± vectors ± fixed formulas. **Maintainer decision (checklist item 10.1 / EPIC-051):** shipped stack stays **embedded SQLite–first** (BM25 + built-in sqlite-vec hybrid); **learned sparse**, **ColBERT-style** late interaction, and **managed external vector DB** as first-class backends are **out of scope for core** until revisited — see [`ADR-001`](../planning/adr/ADR-001-retrieval-stack.md).

---

## 2. Storage, persistence, and schema

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **Per-agent embedded store** | **SQLite** (isolated per agent) | Each agent: `{project}/.tapps-brain/agents/{agent_id}/memory.db`; legacy shared: `memory/memory.db`. WAL mode. `persistence.py`. No cross-agent contention — each agent has its own DB + lock. (EPIC-053) |
| **Shared store (Hive)** | **PostgreSQL** (prod) or **SQLite** (dev) | `postgres_hive.py` (`PostgresHiveBackend`) with `pgvector`, `tsvector`, `LISTEN/NOTIFY`, connection pooling. SQLite fallback: `hive.py` (`HiveStore`). Backend selected by `create_hive_backend(dsn)` — Postgres when DSN starts with `postgres://`. (EPIC-054/055) |
| **Shared store (Federation)** | **PostgreSQL** (prod) or **SQLite** (dev) | `postgres_federation.py` (`PostgresFederationBackend`). SQLite fallback: `federation.py`. Factory: `create_federation_backend(dsn)`. (EPIC-054/055) |
| **Backend abstraction** | **Protocol** + **factory** pattern | `_protocols.py` defines `HiveBackend`, `FederationBackend`, `AgentRegistryBackend`. `backends.py` provides `create_hive_backend()` / `create_federation_backend()` factories + `SqliteHiveBackend` / `SqliteFederationBackend` adapters. Callers never import a concrete backend. (EPIC-054) |
| **Postgres connection pooling** | **psycopg** + **psycopg_pool** | `postgres_connection.py` (`PostgresConnectionManager`). Configurable pool min/max via `TAPPS_BRAIN_HIVE_POOL_MIN`/`MAX`. Lazy import — only required when using Postgres. (EPIC-055) |
| **Postgres schema migrations** | Versioned SQL + runner | `postgres_migrations.py` — `apply_hive_migrations()`, `apply_federation_migrations()`. SQL files in `src/tapps_brain/migrations/hive/` and `migrations/federation/`. Forward-only, idempotent. CLI: `maintenance migrate-hive` / `hive-schema-status`. Auto-migrate: `TAPPS_BRAIN_HIVE_AUTO_MIGRATE`. (EPIC-055) |
| **Local declarative migrations** | Versioned schema | `persistence.py` `_ensure_schema()` — current **v17** includes `embedding_model_id`, `memory_group`, temporal fields, embeddings, etc. (`data-stores-and-schema.md`). |
| **Full-text index** | **FTS5** (local) / **tsvector** (Postgres) | Local: `memories_fts`, session FTS via FTS5 + sync triggers. Hive Postgres: `tsvector` GIN index + `plainto_tsquery()`. |
| **Structured config / validation** | **Pydantic v2** | `models.py`, `profile.py`, API payloads. |
| **Structured logging** | **structlog** | Used across store, retrieval, MCP, CLI. |
| **Encryption at rest (optional)** | **SQLCipher** via **`pysqlcipher3`** (`[encryption]` extra) | `sqlcipher_util.py`, optional encrypted connections for local SQLite stores. Postgres: use PostgreSQL native TLS + `pg_tde` if needed. Operator runbook: [`sqlcipher.md`](../guides/sqlcipher.md). **Checklist 10.5:** [`ADR-005`](../planning/adr/ADR-005-sqlcipher-key-backup-operations.md). |
| **Append-only audit** | JSONL | `audit.py` / project `memory_log.jsonl` (see store initialization paths). |

---

## 3. Ingestion, deduplication, and lifecycle

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **Write-time content safety** | Rule-based **RAG safety** | `safety.py` — pattern checks, sanitize/block on save and before injection. |
| **Near-duplicate detection** | **Bloom filter** + normalized text | `bloom.py` + `normalize_for_dedup` (NFKC, lower, whitespace) on save path; nominal FP ~`fp_rate` at `expected_items` load (see `bloom_false_positive_probability`); may **reinforce** existing key instead of new row. |
| **Contradiction / conflict handling** | Heuristic **save-time conflicts** | `contradictions.py` (`detect_save_conflicts`, `SaveConflictHit`) + `store.save(..., conflict_check=True)` — temporal invalidation, `contradicted` + `contradiction_reason`; threshold from `profile.conflict_check` / `ConflictCheckConfig` (GitHub #44, EPIC-044.3). Offline: `maintenance save-conflict-candidates`, [`save-conflict-nli-offline.md`](../guides/save-conflict-nli-offline.md). **Checklist 10.3:** core stays heuristic + offline/opt-in review; curated ontology and in-product review MCP queue **deferred** — [`ADR-003`](../planning/adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md). |
| **Deterministic merge / consolidation** | **Jaccard**, **TF-IDF**, topic flags | `consolidation.py`, `similarity.py`, `auto_consolidation.py` — merge similar entries **without LLM**; auto path on save when enabled (`ConsolidationConfig`); JSONL audit ``consolidation_merge`` / ``consolidation_source`` / ``consolidation_merge_undo``; ``MemoryStore.undo_consolidation_merge``; CLI ``tapps-brain maintenance consolidation-merge-undo`` (EPIC-044.4). Consolidated saves use ``skip_consolidation=True``. Read-only threshold sensitivity: `evaluation.run_consolidation_threshold_sweep`; CLI `tapps-brain maintenance consolidation-threshold-sweep`. |
| **Garbage collection / archival** | Tier-aware GC | `gc.py`, profile `GCConfig`, CLI/MCP maintenance paths. |
| **Profile-driven seeding** | External **project profile** shape | `seeding.py` — seeds only empty store (first run); `reseed_from_profile` updates `auto-seeded` tags only. Optional `profile.seeding.seed_version` echoed in seed summaries and on `StoreHealthReport` / `maintenance health` / `memory://stats` as `profile_seed_version`. |
| **Caps** | Max entries per project | Default 5000; profile `limits.max_entries`; optional `limits.max_entries_per_group` (per `memory_group` bucket + ungrouped); eviction / fair global behavior: [`data-stores-and-schema.md`](data-stores-and-schema.md#entry-cap-and-eviction-runtime). |

---

## 4. Multi-tenant, sharing, and sync models

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **Per-agent brain identity** | Isolated per-agent SQLite stores | `MemoryStore(agent_id="frontend-dev")` → `{project}/.tapps-brain/agents/frontend-dev/memory.db`. Auto-registration in `AgentRegistry`. `source_agent` auto-fill on saves. CLI/MCP `--agent-id` passthrough. Migration: `maintenance split-by-agent`. (EPIC-053) |
| **Cross-agent shared memory** | **Hive** (PostgreSQL or SQLite + namespaces) | `PostgresHiveBackend` (prod) or `SqliteHiveBackend` (dev). `PropagationEngine` routes by `agent_scope`. Backend factory: `create_hive_backend(dsn)`. Guides: [`hive.md`](../guides/hive.md), [`hive-deployment.md`](../guides/hive-deployment.md). (EPIC-054/055) |
| **Declarative group membership** | Groups + expert auto-publish | `MemoryStore(groups=["dev-pipeline"], expert_domains=["react"])`. Groups auto-created in Hive. Expert agents auto-publish `architectural`/`pattern` tier saves. Profile YAML: `hive.groups` / `hive.expert_domains`. (EPIC-056) |
| **Cross-project hub** | **Federation** (PostgreSQL or SQLite + explicit sync) | `PostgresFederationBackend` (prod) or `SqliteFederationBackend` (dev). Publish/subscribe/pull; **not** continuous background replication. (EPIC-054/055) |
| **Agent / scope routing** | String **`agent_scope`** (+ `group:<name>`) | `agent_scope.py`, propagation rules in profile `HiveConfig`, recall namespace union in `recall.py`. |
| **Project-local partitioning** | **`memory_group`** column | Filter/list/recall/MCP/CLI; distinct from Hive namespace (see `memory-scopes.md`). |
| **Change notification** | **LISTEN/NOTIFY** (Postgres) or monotonic revision + sidecar file (SQLite) | Postgres: real-time `LISTEN/NOTIFY` in `PostgresHiveBackend`. SQLite: `hive_write_notify`, MCP `hive_write_revision` / `hive_wait_write`, CLI `hive watch`. |

---

## 5. Agent / tool integration

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **AgentBrain facade** | `AgentBrain` class (EPIC-057) | `agent_brain.py` — 5 methods: `remember()`, `recall()`, `forget()`, `learn_from_success()`, `learn_from_failure()`. Configured via env vars (`TAPPS_BRAIN_AGENT_ID`, `TAPPS_BRAIN_HIVE_DSN`, `TAPPS_BRAIN_GROUPS`, `TAPPS_BRAIN_EXPERT_DOMAINS`) or constructor. Context manager. Agents never import `MemoryStore` directly. Guides: [`agent-integration.md`](../guides/agent-integration.md), [`llm-brain-guide.md`](../guides/llm-brain-guide.md). |
| **MCP server** | **`mcp`** SDK (`[mcp]` extra) | `mcp_server.py` — tool/resource/prompt surface; simplified `brain_*` MCP tools matching `AgentBrain` vocabulary; `--agent-id` passthrough; manifest: `docs/generated/mcp-tools-manifest.json`. |
| **CLI** | **Typer** (`[cli]` extra) | `cli.py` — `tapps-brain` entry point; `--agent-id` for per-agent ops; agent-friendly aliases; store helper attaches embedding provider + configured Hive backend. |
| **Docker deployment** | `docker-compose.hive.yaml` (EPIC-058) | Postgres container (pgvector/pgvector:pg17), auto-schema init, health checks, backup/restore. Reference compose for agent containers. See [`hive-deployment.md`](../guides/hive-deployment.md), [`agentforge-integration.md`](../guides/agentforge-integration.md). |
| **Portable interchange** | **YAML** + **JSON** | Agent registry YAML; relay JSON (`memory_relay.py`); profile YAML under `profiles/`. |

---

## 6. Quality loop, observability, and ops

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **User/agent feedback signals** | Typed events + optional strict mode | `feedback.py`, `FeedbackStore`, profile `FeedbackConfig`. |
| **Diagnostics / SLO-style scorecard** | Deterministic composite + **EWMA** anomalies | `diagnostics.py`, circuit breaker behavior, MCP/CLI diagnostics tools. |
| **Flywheel (confidence updates)** | Bayesian-style updates + reports | `flywheel.py` — optional **LLM-as-judge** backends detected via `_feature_flags.py` (`openai`, `anthropic`) for **offline/reporting** paths, not core retrieve. |
| **Health checks** | Aggregated store + Hive + retrieval mode | `health_check.py` — `retrieval_effective_mode`, `retrieval_summary`, sqlite-vec fields (#63). Save-phase timing: `save_phase_summary` + full histograms via `get_metrics()` / MCP **`memory://metrics`**. **Checklist 10.6:** [`ADR-006`](../planning/adr/ADR-006-save-path-observability.md). |
| **Distributed tracing (optional)** | **OpenTelemetry** (`[otel]` extra) | `otel_exporter.py` — exporter creation when deps present; wiring documented as optional (`optional-features-matrix.md`, observability guide). |
| **Rate limiting** | In-process **sliding window** | `rate_limiter.py` + `RateLimiterConfig` on `MemoryStore`. |
| **Integrity** | Per-entry **hash** | `integrity.py` — `integrity_hash` on `MemoryEntry`. |

---

## 7. Optional / auxiliary capabilities

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **Session memory** | Session index + FTS | `session_index.py`, `session_summary.py`, CLI/MCP session end. |
| **Graph-like links** | Relations table | `relations.py`, `extract_relations` on save. |
| **Markdown round-trip** | Import/sync helpers | `markdown_import.py`, `markdown_sync.py`. |
| **Evaluation harness** | **BEIR-style** deterministic metrics | `evaluation.py` (regression / quality experiments, not runtime MCP). |
| **Doc validation** | Pluggable lookup | `doc_validation.py` with `LookupEngineLike` protocol. |
| **Visual snapshot (operator)** | Documented optional path | See `docs/guides/visual-snapshot.md` (#59). |

---

## 8. Dependency extras (install surface)

| Extra (`pyproject.toml`) | Packages | Purpose |
|--------------------------|----------|---------|
| `cli` | `typer` | Command-line interface. |
| `mcp` | `mcp` | MCP server. |
| `reranker` | `flashrank` | Local cross-encoder re-ranking. |
| `encryption` | `pysqlcipher3` | SQLCipher (local SQLite encryption). |
| `otel` | `opentelemetry-api`, `opentelemetry-sdk` | Telemetry export. |
| **Core** | `pydantic`, `structlog`, `pyyaml`, `numpy`, `sentence-transformers`, `sqlite-vec` | Always installed (vector search built-in since v2.2.0). |
| **Postgres** (lazy) | `psycopg[binary]`, `psycopg_pool` | Required only when using `postgres://` DSN. Not a declared extra — lazy-imported with helpful error message if missing. Install: `pip install 'psycopg[binary]' psycopg_pool`. |

Lazy detection: `_feature_flags.py` probes importability for optional LLM SDKs (`anthropic_sdk`, `openai_sdk`). Postgres deps detected lazily in `postgres_connection.py`.

---

## 9. Concurrency and runtime model

| Topic | What we use |
|-------|-------------|
| **Async** | **None in core** — synchronous API by design. |
| **Per-agent isolation** | Each agent gets its own `MemoryStore` + own SQLite DB + own `threading.Lock`. **No cross-agent contention** for private memory. 200 agents = 200 independent stores. (EPIC-053) |
| **Shared store concurrency** | **PostgreSQL MVCC** for Hive/Federation in production — concurrent reads/writes from N agents. Connection pooling via `psycopg_pool`. SQLite fallback for local dev (single-writer). (EPIC-055) |
| **Thread safety** | **`threading.Lock`** per `MemoryStore` instance — scoped to one agent, not shared. |
| **Agent-local SQLite** | **WAL** mode; optional RO search connection (`TAPPS_SQLITE_MEMORY_READONLY_SEARCH`); configurable busy timeout (`TAPPS_SQLITE_BUSY_MS`). |
| **Scale posture** | **Per-agent SQLite + shared Postgres Hive** handles 200+ concurrent agents. Per-agent tuning via [`system-architecture.md`](system-architecture.md) *Concurrency model*. Further service extraction deferred: [`ADR-004`](../planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md). |

---

## 10. Review checklist (improvement framing)

Use this list when comparing to industry alternatives:

1. **Retrieval:** **Decision (2026-04-03, updated 2026-04-07):** BM25 + built-in dense (sqlite-vec, core since v2.2.0) + RRF **is the maintained stack** for core; learned sparse, ColBERT, and managed vector DB are **deferred / out of scope for shipped core** — [`ADR-001`](../planning/adr/ADR-001-retrieval-stack.md).
2. **Freshness:** **Decision (2026-04-03):** **Lazy decay on read** + **profile / consolidation tuning** + **operator-invoked GC** (`gc.py`, `maintenance gc` / `maintenance stale`) **are the maintained model**; mandatory wall-clock TTL workers, `maintenance decay-refresh`, and daily “crossed stale threshold” metrics **deferred** — [`ADR-002`](../planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md).
3. **Correctness:** **Decision (2026-04-03):** **Heuristic save-time conflicts** + **offline** candidate export / opt-in external review ([`save-conflict-nli-offline.md`](../guides/save-conflict-nli-offline.md)) **are the maintained model** for core; **curated ontology**, automatic **`needs_review` queues**, and **MCP list/resolve review** workflows **deferred** until explicit product spec + planning trigger **(c)** — [`ADR-003`](../planning/adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md).
4. **Scale:** **Decision (2026-04-03, updated 2026-04-09):** **Per-agent isolated SQLite + shared Postgres Hive** (EPIC-053/055) is the maintained posture for 200+ agents. Per-agent: own `memory.db` + own lock (no cross-agent contention). Shared: PostgreSQL with MVCC, connection pooling, pgvector. Operator tuning (**WAL**, `busy_timeout`, optional RO search, lock timeout) applies per agent. **Published QPS SLO** and **mandatory service extraction** **deferred** until evidence — [`ADR-004`](../planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md).
5. **Security:** **Decision (2026-04-03):** **Passphrase + env / CLI** (`encrypt-db`, `rekey-db`, `decrypt-db`) + **runbook** ([`sqlcipher.md`](../guides/sqlcipher.md): key loss, backup/verify, optional re-key drill) **are maintained**; **vendor-specific KMS envelope** docs **deferred** (host-owned integration) — [`ADR-005`](../planning/adr/ADR-005-sqlcipher-key-backup-operations.md).
6. **Observability:** **Decision (2026-04-03):** **Save-phase histograms** (`store.save.phase.*`), **`get_metrics()`**, MCP **`memory://metrics`**, and **`save_phase_summary`** on health **are the maintained save-path surface**; **deeper** consolidation/GC correlation metrics **deferred** unless **PLANNING.md** trigger **(a)**; **OpenTelemetry** remains a separate optional track ([`EPIC-032`](../planning/epics/EPIC-032.md)) — [`ADR-006`](../planning/adr/ADR-006-save-path-observability.md).

---

## Change log

- **2026-04-09:** Sections 2, 4, 5, 8, 9, 10 — reflect EPIC-053–058 (per-agent SQLite, Postgres Hive/Federation, backend abstraction, AgentBrain API, Docker deployment, group membership). Section 2 rewritten for per-agent stores + Postgres shared backends. Section 4 adds per-agent identity, declarative groups, Postgres notifications. Section 5 adds AgentBrain facade, Docker deployment. Section 8 adds Postgres lazy deps. Section 9 rewritten for per-agent isolation + Postgres concurrency. Section 10.4 updated for per-agent + Postgres scale posture.
- **2026-04-03:** Section 10 item 6 + section 6 health row — save-path observability ([`ADR-006`](../planning/adr/ADR-006-save-path-observability.md)); phase histograms + metrics + health summary maintained; defer deeper metrics unless trigger **(a)**; OTel remains optional.
- **2026-04-03:** Section 10 item 5 + section 2 encryption row — SQLCipher ops ([`ADR-005`](../planning/adr/ADR-005-sqlcipher-key-backup-operations.md)); `sqlcipher.md` backup/verify + key-loss + enterprise KMS note; defer vendor KMS how-tos.
- **2026-04-03:** Section 10 item 4 + section 9 scale row — scale posture ([`ADR-004`](../planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md)); single-node maintained; defer QPS SLO + service extraction until evidence.
- **2026-04-03:** Section 10 item 3 + section 3 contradiction row — correctness boundary ([`ADR-003`](../planning/adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md)); heuristic conflicts + offline review; defer ontology / review-queue MCP (trigger **(c)** in `PLANNING.md`).
- **2026-04-03:** Section 10 item 2 + section 1 stale row — freshness decision ([`ADR-002`](../planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md)); lazy decay + operator GC; defer TTL jobs / decay-refresh command / daily stale-crossing metrics.
- **2026-04-03:** Section 10 item 1 + section 1 boundaries — retrieval stack maintainer decision ([`ADR-001`](../planning/adr/ADR-001-retrieval-stack.md)); embedded SQLite–first; defer learned sparse / ColBERT / managed vector DB for core.
- **2026-03-31:** Linked improvement program index (EPIC-042–051) for section-by-section research stories.
- **2026-03-31:** Initial map for architecture review (features ↔ technologies ↔ modules).
