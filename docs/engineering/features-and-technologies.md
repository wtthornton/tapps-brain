# Industry features and technologies (implementation map)

**Audience:** Architecture and product review — what capability areas we cover, which libraries/patterns we use, and where behavior lives in code.

**Improvement program (epics + stories):** [`docs/planning/epics/EPIC-042-feature-tech-index.md`](../planning/epics/EPIC-042-feature-tech-index.md) — maps each section here to **EPIC-042** … **EPIC-051** with research notes and implementation themes.

**Related:** [`optional-features-matrix.md`](optional-features-matrix.md), [`data-stores-and-schema.md`](data-stores-and-schema.md), [`call-flows.md`](call-flows.md), [`system-architecture.md`](system-architecture.md).

---

## 1. Retrieval and ranking (RAG-style memory)

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **Lexical / keyword search** | SQLite **FTS5** + in-process **Okapi BM25** | FTS for candidate generation / filtering paths; `bm25.py` implements BM25 with stop-word stripping and light normalization (pure Python, no IR server). |
| **Dense retrieval / semantic search** | **`sentence-transformers`** + **`numpy`** + optional **`faiss-cpu`** | Embeddings computed in `embeddings.py` when `[vector]` extra installed; vectors stored on entries and optionally in sqlite-vec table. **Model card:** [`embedding-model-card.md`](../guides/embedding-model-card.md). |
| **Vector index in DB** | **`sqlite-vec`** (`vec0`, table `memory_vec`) | `persistence.py` / `sqlite_vec_index.py`; KNN path when extension + embeddings available; health reports `sqlite_vec_enabled` / row counts. Ops: [`sqlite-vec-operators.md`](../guides/sqlite-vec-operators.md). |
| **Hybrid search** | **Reciprocal Rank Fusion (RRF)** | `fusion.py` merges BM25-ranked and vector-ranked lists; **weighted RRF** via `hybrid_rrf_weights_for_query()` (GitHub #40) — deterministic query heuristics, no LLM. Per-channel recall depth and RRF *k* are optional under **`profile.hybrid_fusion`** (`HybridFusionConfig` in `profile.py`; YAML aliases `top_k_lexical` / `top_k_dense`). |
| **Composite ranking** | Weighted score blend | `retrieval.py`: relevance 40%, confidence 30%, recency 15%, frequency 15%; per-source trust multipliers after composite; profile can tune scoring where wired. |
| **Re-ranking (cross-encoder API)** | **Cohere** (`[reranker]` extra) | `reranker.py`; used in injection pipeline when configured; falls back to noop. |
| **Token-budgeted context** | Fixed caps + estimates | `injection.py`: `InjectionConfig.injection_max_tokens` (default 2000), per-tier max inject counts, `_MIN_SCORE` floor before inject. |
| **Stale / decayed relevance** | **Exponential decay** + optional **FSRS-like fields** | `decay.py` lazy decay on read; `models.py` carries `stability` / `difficulty`; hybrid model + recall vs reinforce updates in [`memory-decay-and-fsrs.md`](../guides/memory-decay-and-fsrs.md). **Checklist 10.2:** lazy decay + operator GC — no mandatory wall-clock TTL jobs in core; [`ADR-002`](../planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md). |

**Explicit boundaries:** Core retrieval does **not** call an LLM to score documents. “Relevance” is BM25 ± vectors ± fixed formulas. **Maintainer decision (checklist item 10.1 / EPIC-051):** shipped stack stays **embedded SQLite–first** (BM25 + optional `[vector]` / sqlite-vec hybrid); **learned sparse**, **ColBERT-style** late interaction, and **managed external vector DB** as first-class backends are **out of scope for core** until revisited — see [`ADR-001`](../planning/adr/ADR-001-retrieval-stack.md).

---

## 2. Storage, persistence, and schema

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **Embedded OLTP store** | **SQLite** | Project `memory.db`, Hive `hive.db`, federation `federated.db`; WAL where configured (`persistence.py`, `hive.py`, `federation.py`). |
| **Declarative migrations** | Versioned schema | `persistence.py` `_ensure_schema()` — current **v17** includes `embedding_model_id`, `memory_group`, temporal fields, embeddings, etc. (`data-stores-and-schema.md`). |
| **Full-text index** | **FTS5** + sync triggers | `memories_fts`, session FTS, Hive `hive_fts`, federation `federated_fts`. |
| **Structured config / validation** | **Pydantic v2** | `models.py`, `profile.py`, API payloads. |
| **Structured logging** | **structlog** | Used across store, retrieval, MCP, CLI. |
| **Encryption at rest (optional)** | **SQLCipher** via **`pysqlcipher3`** (`[encryption]` extra) | `sqlcipher_util.py`, optional encrypted connections in persistence / Hive / Feedback / diagnostics stores; CLI maintenance encrypt/rekey/decrypt. Operator runbook: [`sqlcipher.md`](../guides/sqlcipher.md) (key env vars, backup/restore checklist, KMS note). **Checklist 10.5:** [`ADR-005`](../planning/adr/ADR-005-sqlcipher-key-backup-operations.md). |
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
| **Cross-agent shared memory** | **Hive** (separate SQLite DB + namespaces) | `hive.py` — `HiveStore`, `PropagationEngine`, group membership, FTS5 per store; CLI/MCP attach `HiveStore` by default (see guides). |
| **Cross-project hub** | **Federation** (hub SQLite + explicit sync) | `federation.py` — publish/subscribe/pull; **not** continuous background replication; optional `memory_group` on hub rows (#51). |
| **Agent / scope routing** | String **`agent_scope`** (+ `group:<name>`) | `agent_scope.py`, propagation rules in profile `HiveConfig`, recall namespace union in `recall.py`. |
| **Project-local partitioning** | **`memory_group`** column | Filter/list/recall/MCP/CLI; distinct from Hive namespace (see `memory-scopes.md`). |
| **Change notification (polling)** | Monotonic revision + sidecar file | Hive `hive_write_notify`, MCP `hive_write_revision` / `hive_wait_write`, CLI `hive watch`. |

---

## 5. Agent / tool integration

| Industry feature | What we use | How (implementation) |
|------------------|-------------|-------------------------|
| **MCP server** | **`mcp`** SDK (`[mcp]` extra) | `mcp_server.py` — tool/resource/prompt surface; manifest: `docs/generated/mcp-tools-manifest.json` (64 tools, 8 resources as of generation). |
| **CLI** | **Typer** (`[cli]` extra) | `cli.py` — `tapps-brain` entry point; store helper attaches embedding provider + `HiveStore` for typical commands. |
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
| `vector` | `faiss-cpu`, `numpy`, `sentence-transformers`, `sqlite-vec` | Embeddings, optional FAISS, sqlite-vec extension binding. |
| `reranker` | `cohere` | API re-ranking. |
| `encryption` | `pysqlcipher3` | SQLCipher. |
| `otel` | `opentelemetry-api`, `opentelemetry-sdk` | Telemetry export. |
| **Core** | `pydantic`, `structlog`, `pyyaml` | Always installed. |

Lazy detection: `_feature_flags.py` probes importability for vector, sqlite_vec, otel, optional LLM SDKs.

---

## 9. Concurrency and runtime model

| Topic | What we use |
|-------|-------------|
| **Async** | **None in core** — synchronous API by design. |
| **Thread safety** | **`threading.Lock`** in `MemoryStore` and persistence-critical sections. |
| **SQLite** | **WAL** mode where enabled; single-writer semantics typical of SQLite. |
| **Scale posture** | **Single process / single-node SQLite** as default unit; tuning + workload separation before architecture split — [`system-architecture.md`](system-architecture.md) *Concurrency model*; **checklist 10.4** [`ADR-004`](../planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md). |

---

## 10. Review checklist (improvement framing)

Use this list when comparing to industry alternatives:

1. **Retrieval:** **Decision (2026-04-03):** BM25 + optional dense (`[vector]` / sqlite-vec) + RRF **is the maintained stack** for core; learned sparse, ColBERT, and managed vector DB are **deferred / out of scope for shipped core** — [`ADR-001`](../planning/adr/ADR-001-retrieval-stack.md).
2. **Freshness:** **Decision (2026-04-03):** **Lazy decay on read** + **profile / consolidation tuning** + **operator-invoked GC** (`gc.py`, `maintenance gc` / `maintenance stale`) **are the maintained model**; mandatory wall-clock TTL workers, `maintenance decay-refresh`, and daily “crossed stale threshold” metrics **deferred** — [`ADR-002`](../planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md).
3. **Correctness:** **Decision (2026-04-03):** **Heuristic save-time conflicts** + **offline** candidate export / opt-in external review ([`save-conflict-nli-offline.md`](../guides/save-conflict-nli-offline.md)) **are the maintained model** for core; **curated ontology**, automatic **`needs_review` queues**, and **MCP list/resolve review** workflows **deferred** until explicit product spec + planning trigger **(c)** — [`ADR-003`](../planning/adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md).
4. **Scale:** **Decision (2026-04-03):** **Single-node SQLite + lock** + documented operator tuning (**WAL**, `busy_timeout`, optional RO search, lock timeout) **is the maintained posture**; **published QPS SLO** and **mandatory service extraction** **deferred** until evidence (benchmarks / production pain) — [`ADR-004`](../planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md).
5. **Security:** **Decision (2026-04-03):** **Passphrase + env / CLI** (`encrypt-db`, `rekey-db`, `decrypt-db`) + **runbook** ([`sqlcipher.md`](../guides/sqlcipher.md): key loss, backup/verify, optional re-key drill) **are maintained**; **vendor-specific KMS envelope** docs **deferred** (host-owned integration) — [`ADR-005`](../planning/adr/ADR-005-sqlcipher-key-backup-operations.md).
6. **Observability:** **Decision (2026-04-03):** **Save-phase histograms** (`store.save.phase.*`), **`get_metrics()`**, MCP **`memory://metrics`**, and **`save_phase_summary`** on health **are the maintained save-path surface**; **deeper** consolidation/GC correlation metrics **deferred** unless **PLANNING.md** trigger **(a)**; **OpenTelemetry** remains a separate optional track ([`EPIC-032`](../planning/epics/EPIC-032.md)) — [`ADR-006`](../planning/adr/ADR-006-save-path-observability.md).

---

## Change log

- **2026-04-03:** Section 10 item 6 + section 6 health row — save-path observability ([`ADR-006`](../planning/adr/ADR-006-save-path-observability.md)); phase histograms + metrics + health summary maintained; defer deeper metrics unless trigger **(a)**; OTel remains optional.
- **2026-04-03:** Section 10 item 5 + section 2 encryption row — SQLCipher ops ([`ADR-005`](../planning/adr/ADR-005-sqlcipher-key-backup-operations.md)); `sqlcipher.md` backup/verify + key-loss + enterprise KMS note; defer vendor KMS how-tos.
- **2026-04-03:** Section 10 item 4 + section 9 scale row — scale posture ([`ADR-004`](../planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md)); single-node maintained; defer QPS SLO + service extraction until evidence.
- **2026-04-03:** Section 10 item 3 + section 3 contradiction row — correctness boundary ([`ADR-003`](../planning/adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md)); heuristic conflicts + offline review; defer ontology / review-queue MCP (trigger **(c)** in `PLANNING.md`).
- **2026-04-03:** Section 10 item 2 + section 1 stale row — freshness decision ([`ADR-002`](../planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md)); lazy decay + operator GC; defer TTL jobs / decay-refresh command / daily stale-crossing metrics.
- **2026-04-03:** Section 10 item 1 + section 1 boundaries — retrieval stack maintainer decision ([`ADR-001`](../planning/adr/ADR-001-retrieval-stack.md)); embedded SQLite–first; defer learned sparse / ColBERT / managed vector DB for core.
- **2026-03-31:** Linked improvement program index (EPIC-042–051) for section-by-section research stories.
- **2026-03-31:** Initial map for architecture review (features ↔ technologies ↔ modules).
