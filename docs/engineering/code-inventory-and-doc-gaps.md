# Code Inventory and Documentation Gaps

## Inventory by subsystem

All source modules live in `src/tapps_brain/`. 80+ files organized into 9 layers.

### Core memory & storage
- `store.py` — `MemoryStore`: in-memory cache + Postgres write-through, thread-safe via `threading.Lock`; RAG safety, auto-consolidation, maintenance, diagnostics, flywheel
- `models.py` — Pydantic v2 models: `MemoryEntry`, `RecallResult`, `RecallDiagnostics`, `MemorySnapshot`, `MemoryTier`, `MemoryScope`, `MemorySource`, `ConsolidatedEntry`, `AgentRegistration`; `temporal_sensitivity` (TAP-735); `failed_approaches` (TAP-731); `status`/`stale_reason` (TAP-732)
- `agent_brain.py` — `AgentBrain` facade: 5-method API (`remember`, `recall`, `forget`, `learn_from_success`, `learn_from_failure`); env-var or constructor config; context manager
- `aio.py` — `AsyncMemoryStore`: thin `asyncio.to_thread` wrapper for callers needing an async surface (EPIC-067)

### Retrieval & search
- `retrieval.py` — `MemoryRetriever`: composite BM25/vector scoring; `MemoryFilter` pre-filters (TAP-733)
- `recall.py` — `RecallOrchestrator`: pre-prompt memory injection; `RecallConfig`
- `bm25.py` — Okapi BM25 (pure Python, stop-word stripping, IDF over full corpus)
- `lexical.py` — tokenization for BM25 and tsvector query building
- `fusion.py` — Reciprocal Rank Fusion (RRF) for hybrid BM25 + vector; `hybrid_rrf_weights_for_query()`
- `reranker.py` — optional FlashRank cross-encoder reranker (`[reranker]` extra)
- `embedding.py` — embedding utilities; `SentenceTransformerProvider` with commit SHA pinning (TAP-720)
- `similarity.py` — Jaccard + TF-IDF cosine similarity for consolidation detection
- `injection.py` — token-budgeted memory injection; `InjectionConfig`

### Persistence & schema
- `postgres_private.py` — `PostgresPrivateBackend`: `private_memories` table, keyed by `(project_id, agent_id, key)`; migrations 001–014
- `postgres_connection.py` — `PostgresConnectionManager`: connection pooling via `psycopg` + `psycopg_pool`
- `postgres_hive.py` — `PostgresHiveBackend`: pgvector HNSW + tsvector + `LISTEN/NOTIFY`; `PostgresAgentRegistry`
- `postgres_federation.py` — `PostgresFederationBackend`: cross-project memory sharing, parameterized SQL, JSONB tags
- `postgres_migrations.py` — versioned migration runner; `apply_private_migrations`, `apply_hive_migrations`, `apply_federation_migrations`
- `backends.py` — factory functions: `create_private_backend`, `create_hive_backend`, `create_federation_backend`, `create_agent_registry_backend`, `resolve_*_from_env`
- `_protocols.py` — Protocol interfaces: `PrivateBackend`, `HiveBackend`, `FederationBackend`, `AgentRegistryBackend`

### Memory lifecycle
- `decay.py` — exponential decay with tier-specific half-lives; lazy (evaluated on read); `DecayError` on malformed timestamps (TAP-725)
- `consolidation.py` — deterministic merge via Jaccard + TF-IDF (no LLM); JSONL audit trail; `undo_consolidation_merge`
- `auto_consolidation.py` — auto-consolidation trigger on save
- `gc.py` — garbage collection and archival for stale entries; `GCConfig`
- `reinforcement.py` — reinforcement system to reset decay on access
- `promotion.py` — tier promotion/demotion engine
- `tier_normalize.py` — normalize tier strings from agents/relays/profiles
- `write_policy.py` — pluggable write-path policy (`batch` vs `streaming`)
- `seeding.py` — profile-based memory seeding; `seed_version` label (EPIC-044.6)
- `bloom.py` — Bloom filter for fast duplicate detection; `clear()`/`remove()`/resize on growth (TAP-726)

### Hive & federation (multi-agent)
- `agent_scope.py` — `agent_scope` normalization for Hive routing (`private`/`domain`/`hive`/`group:<name>`)
- `memory_group.py` — project-local memory partition labels
- `memory_relay.py` — structured memory relay format for cross-node handoff

### Feedback & observability
- `feedback.py` — `FeedbackEvent`, `FeedbackStore`, `InMemoryFeedbackStore`
- `flywheel.py` — Bayesian confidence updates, gap tracking, markdown reports; optional `LLMJudge` backends (EPIC-031)
- `diagnostics.py` — composite scorecard, EWMA anomaly detection, circuit breaker (EPIC-030)
- `metrics.py` — `MetricsCollector`, `MetricsSnapshot`, `MetricsTimer`
- `otel_tracer.py` — span names, `start_span()` context manager, `extract_trace_context()`
- `otel_exporter.py` — `OTelExporter`, `MemoryBodyRedactionFilter`, `create_allowed_attribute_views()`
- `rate_limiter.py` — sliding window rate limiter; `batch_exempt_scope` contextvar (TAP-714)
- `health_check.py` — `run_health_check`, `StoreHealthReport`; HNSW sanity check on startup (TAP-655)

### Safety & validation
- `safety.py` — content safety and prompt injection detection; sanitize/block on save and before injection
- `integrity.py` — HMAC-SHA256 integrity hashing; atomic key-write with `O_CREAT|O_EXCL` and `0o700` dir (TAP-709/710)
- `doc_validation.py` — Context7-assisted memory validation/enrichment; `LookupEngineLike` protocol
- `contradictions.py` — save-time conflict detection; `detect_save_conflicts`, `SaveConflictHit`
- `idempotency.py` — idempotency key store for HTTP write operations; per-key `asyncio.Lock` (TAP-629)

### Import/export & integration
- `io.py` — import/export for shared memory entries
- `markdown_sync.py` — bidirectional MEMORY.md sync; atomic write via temp-file + rename (TAP-715)
- `markdown_import.py` — markdown import for migrating MEMORY.md files

### Advanced features
- `extraction.py` — rule-based extraction of durable facts from session context
- `relations.py` — entity/relationship extraction (subject-predicate-object); graph centrality scoring (TAP-734)
- `session_summary.py` — end-of-session episodic memory capture
- `session_index.py` — session indexing for searchable past sessions; O(1) upsert, bounded bucket (TAP-640)
- `visual_snapshot.py` — versioned JSON snapshot for brain visual dashboards

### Profiles & configuration
- `profile.py` — `MemoryProfile`, `PromotionThreshold`, `ScoringConfig` (incl. `graph_centrality` weight), `DecayConfig`, `HybridFusionConfig`; `load_profile`
- `project_registry.py` — per-project `MemoryProfile` storage
- `project_resolver.py` — transport-layer `project_id` resolution
- `onboarding.py` — profile-driven onboarding text for agents

### Interfaces & clients
- `cli.py` — Typer-based CLI (`tapps-brain`); sub-apps: store, memory, maintenance, profile, hive, agent, openclaw, feedback, diagnostics, flywheel, visual
- `http_adapter.py` — FastAPI HTTP adapter; routes: `/v1/health`, `/v1/remember`, `/v1/recall`, `/v1/search`, `/v1/list`, `/v1/delete`, `/mcp/`; binds `127.0.0.1` by default (TAP-622)
- `mcp_server/` — FastMCP package (split into 7 submodules in TAP-605): `server.py`, `context.py`, `tools_brain.py`, `tools_memory.py`, `tools_feedback.py`, `tools_hive.py`, `tools_maintenance.py`, `tools_agents.py`, `tools_resources.py`
- `client.py` — `TappsBrainClient` (sync), `AsyncTappsBrainClient` (async); retry backoff capped at 30s (TAP-647); MCP session-initialize handshake (TAP-744)
- `openapi_contract.py` — OpenAPI contract builder with dual auth schemes and tenant headers

### TypeScript packages (`packages/`)
- `packages/sdk/` — `@tapps-brain/sdk` v1.0.0 (TAP-561 / STORY-SC05): TypeScript client exposing the full `brain_*` + `memory_*` MCP surface over Streamable HTTP. Mirrors `client.py`. Guide: [`typescript-sdk.md`](../guides/typescript-sdk.md).
- `packages/langgraph/` — `@tapps-brain/langgraph` v1.0.0 (TAP-561 / STORY-SC05): LangGraph `BaseStore` adapter backed by tapps-brain. Guide: [`langgraph-adapter.md`](../guides/langgraph-adapter.md).

### Utilities & helpers
- `errors.py` — stable error taxonomy for public APIs
- `_feature_flags.py` — minimal feature flags for optional dependencies
- `evaluation.py` — BEIR-style eval harness; `run_consolidation_threshold_sweep`
- `recall_diagnostics.py` — machine-readable codes for empty recall
- `embeddings.py` — embedding provider abstraction

## Known documentation risk areas

- **Hive defaults drift**
  - Some docs showed empty Hive tier defaults; code defaults are non-empty.
- **Hive attach vs profile rules**
  - Resolved for operator docs: see `HiveConfig` docstring and "Who attaches Hive?" in `docs/guides/hive.md` (Phase 2 / #56).
- **Federation `hub_path`**
  - Resolved in code: `federated_hub_db_path()` and default `FederatedStore()` path (#55).
- **Optional feature discoverability**
  - OTel and visual snapshot operator notes live in `docs/guides/observability.md` and `docs/guides/visual-snapshot.md` (#58, #59).

## Dead/stale code workflow (required process)

This baseline does not delete code. Use this workflow:

1. Mark candidate module/path in this file.
2. Confirm no CLI/MCP/library runtime references.
3. Confirm test coverage usage intent.
4. Decide: document, deprecate, or remove.
5. Track with an issue and owner.

## Candidate follow-up audit list

Tracked as **GitHub issues #55–#62** in:

- [`docs/planning/engineering-doc-phase2-follow-up-issues.md`](../planning/engineering-doc-phase2-follow-up-issues.md)

Summary (Phase 2 implementation status, 2026-03-31):

- [x] **#55 / ED-P0-01** — `hub_path` honored (`federated_hub_db_path`, CLI status JSON).
- [x] **#56 / ED-P0-02** — Hive attach story + `HiveConfig` docstring.
- [x] **#57 / ED-P1-01** — Engineering baseline linked from README, CLAUDE.md, `project.mdc`.
- [x] **#58 / ED-P1-02** — `docs/guides/observability.md`; README `[otel]` footnote; EPIC-032 pointer.
- [x] **#59 / ED-P1-03** — `docs/guides/visual-snapshot.md`; README nav link.
- [x] **#60 / ED-P1-04** — Manifest includes resources; docs + OpenClaw check read counts from manifest.
- [x] **#61 / ED-P2-01** — Documented then **removed** `mem0-review/` from the repo (no longer vendored).
- [x] **#62 / ED-P2-02** — Import/static check: `otel_exporter` has no CLI/MCP/store wiring (documented); no new orphan modules filed beyond explicit test-only helpers. Re-run when adding entry points.

- [x] Hive guide/profile defaults reconciled (2026-03-31 baseline).
- [x] Federation guide aligned with `hub_path` behavior (Phase 2).

## Change log

- **2026-04-20:** Full module inventory expanded to all 80+ source files across 9 subsystem layers; updated for TAP-605 (mcp_server/ package), TAP-607 (signal handler), TAP-709–729 (security batch), TAP-731–735 (models: failed_approaches, MemoryStatus, MemoryFilter, graph centrality, temporal_sensitivity), TAP-743–744 (client hotfixes), migrations 001–014.
