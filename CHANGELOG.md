# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## v3.0.0 (2026-04-08)

### Changed

- **Default embedding model:** Switched from `all-MiniLM-L6-v2` to `BAAI/bge-small-en-v1.5` (~10% better retrieval quality, same 384 dimensions).
- **FTS5 tokenizer:** All FTS5 tables now use `porter unicode61` tokenizer for English stemming and Unicode normalization.
- **SQLite pragma audit:** Added `PRAGMA synchronous=NORMAL` to federation connections (was already set for memory and hive).
- **SQLite version warning:** Logs a warning on startup if SQLite < 3.51.3 (WAL-reset corruption bug).

### Removed

- **`TAPPS_SEMANTIC_SEARCH` env var:** Semantic search is now always enabled (sentence-transformers is a core dependency). The opt-out env var has been removed.
- **`[faiss]` optional extra:** FAISS was never used in any code path; sqlite-vec is the sole vector backend. All FAISS references removed from docs.
- **Schema migration history:** 16 incremental migration methods replaced by a single `_create_schema()`. Schema version reset to 1.
- **Sigmoid relevance normalization:** Min-max is the only normalization path.
- **NoopProvider / EmbeddingProvider protocol:** `SentenceTransformerProvider` is the only embedding implementation.
- **Legacy backwards compatibility:** Removed tier enum union logic, decay config legacy field mapping, FTS5 LIKE fallbacks, Cohere v1/v2 shim, schema version guards, and BM25/vector alias fields.

## v2.2.0 (2026-04-07)

### Changed

- **sqlite-vec promoted to core dependency:** `sqlite-vec`, `sentence-transformers`, and `numpy` moved from the `[vector]` optional extra to core `dependencies`. Semantic vector search is now enabled by default on every install — no extra needed.
- **`[vector]` extra renamed to `[faiss]`:** Now contains only `faiss-cpu` for optional FAISS vector indexing.
- **MemoryStore auto-enables embeddings:** `MemoryStore()` automatically creates an embedding provider when none is passed. Pass `embedding_provider=None` to explicitly disable, or set `TAPPS_SEMANTIC_SEARCH=0`.
- **`get_embedding_provider()` defaults to enabled:** `semantic_search_enabled` parameter now defaults to `True`.
- Version bumped to **2.2.0** (new defaults, no breaking API changes).

### Fixed

- **AsyncMemoryStore.reinforce():** Fixed positional argument bug — `confidence_boost` is keyword-only on `MemoryStore.reinforce()` but was passed positionally by the async wrapper.
- **AsyncMemoryStore.audit():** Fixed positional argument bug — `key` is keyword-only on `MemoryStore.audit()` but was passed positionally by the async wrapper.
- **aio.py lint cleanup:** Removed unused imports (`inspect`, `ConsolidationConfig`), moved `Path` to `TYPE_CHECKING` block, added `ANN401` per-file-ignore for the inherently dynamic async wrapper.

### Added

- **AsyncMemoryStore test suite:** 27 new tests in `tests/unit/test_aio.py` covering CRUD, search, recall, lifecycle, maintenance, properties, context manager, and `__getattr__` auto-wrapping.

### Documentation

- Updated README, getting-started guide, embedding model card, sqlite-vec operator playbook, features-and-technologies, ADR-001, EPIC-042, EPIC-049, next-session-prompt, and open-issues-roadmap to reflect sqlite-vec as a core dependency and `[vector]` → `[faiss]` rename.
- README version badge updated to 2.2.0; test count badge updated to 2900+.

## v2.1.0 (2026-04-06)

### Added

- **Issue #66 — Async API wrapper:** New `AsyncMemoryStore` class in `tapps_brain.aio` wraps every public `MemoryStore` method via `asyncio.to_thread()`. Supports `async with await AsyncMemoryStore.open(root)` context manager. Exported from `tapps_brain.__init__`.
- **Issue #67 — Personal-assistant extraction patterns:** 28 new rule-based extraction patterns for preferences, relationships, health/allergies, routines, and short-term context. Activated when profile is `personal-assistant`; repo-brain extraction is unchanged. Patterns route to appropriate PA tiers (identity, long-term, procedural, short-term).
- **Issue #68 — Procedural tier for personal-assistant profile:** New `procedural` layer (30-day half-life) between `long-term` and `short-term`. Fills the 7d→90d decay gap for routines, how-to knowledge, and workflows. Promotion/demotion chain updated: `ephemeral → short-term → procedural → long-term → identity`. Tier aliases added: `how-to`, `routine`, `workflow` → `procedural`.
- **Issue #70 — Temporal query filtering:** `search()`, `recall()`, and the retrieval pipeline accept `since`, `until`, and `time_field` parameters for time-range filtering. SQL-level WHERE clauses on `created_at`/`updated_at`/`last_accessed` for efficient filtering. `RecallConfig` includes temporal fields.
- **Issue #71 — Profile-driven consolidation threshold:** New `ConsolidationProfileConfig` model on `MemoryProfile`. Personal-assistant profile defaults to threshold 0.65 (more conservative than the 0.7 default) to reduce false merges on semantically varied personal data. Precedence: explicit parameter > profile config > hardcoded default.

### Changed

- Version bumped to **2.1.0** (new features, no breaking changes).
- Personal-assistant profile now has **5 layers** (was 4).
- `long-term` layer demotes to `procedural` (was `short-term`); `short-term` promotes to `procedural` (was `long-term`).

### Documentation

- Updated `docs/guides/profile-catalog.md`: personal-assistant layer table, importance tags, and design decisions reflect the procedural tier, consolidation threshold, and 5-layer architecture.

## v2.0.4 (2026-04-05)

### Fixed

- **EPIC-052** — 2026-Q2 full codebase code review sweep landed all 18 stories. Patched issues:
  - **Write-through consistency (store.py):** `MemoryStore.reinforce()` and `MemoryStore.record_access()` persisted updates without rolling back the in-memory cache on exception; now wrap `self._persistence.save(updated)` in try/except and restore the prior entry on failure, matching the invariant already held by `get()`, `delete()`, and `update_fields()`.
  - **Pydantic validator consistency (models.py):** `_validate_memory_group` now raises `ValueError` (not `TypeError`) for non-string input, matching every other Pydantic validator in `MemoryEntry`.
  - **Feature-flag docstring (_feature_flags.py):** `as_dict()` now documents all 8 flags evaluated (faiss, numpy, sentence_transformers, sqlite_vec, memory_semantic_search, anthropic_sdk, openai_sdk, otel); previously listed only 5.
  - **CLI exit-code drift (cli.py):** `tapps-brain visual export --privacy <invalid>` now exits with code 1 (user error) instead of 2, matching the file-wide convention.
- **Auto-consolidation:** Persisting a merged row uses `skip_consolidation=True` so saving the consolidated entry does not immediately trigger another merge pass.

### Added

- **EPIC-044 STORY-044.3 (offline):** `evaluation.run_save_conflict_candidate_report` and CLI `tapps-brain maintenance save-conflict-candidates` (`--json`, `--threshold`, `--include-contradicted`) to export deterministic save-time conflict pairs for external NLI review — no model on the sync `MemoryStore.save` path. Guide: `docs/guides/save-conflict-nli-offline.md`.
- **EPIC-044 STORY-044.4:** Deterministic **merge undo** — `MemoryStore.undo_consolidation_merge` / `auto_consolidation.undo_consolidation_merge`, JSONL audit action `consolidation_merge_undo`, CLI `tapps-brain maintenance consolidation-merge-undo CONSOLIDATED_KEY` (`--json`). Uses the last matching `consolidation_merge` row and strict validation on superseded sources.
- **EPIC-044 operator surfaces:** `StoreHealthReport.profile_seed_version` (from `MemoryProfile.seeding.seed_version`); text `tapps-brain maintenance health` prints it when set; JSON health and native `run_health_check` expose `profile_seed_version`; MCP resource `memory://stats` includes `profile_seed_version`.
- **CLI:** `tapps-brain maintenance consolidation-threshold-sweep` — read-only consolidation sensitivity report (`evaluation.run_consolidation_threshold_sweep`), optional `--thresholds`, `--min-group-size`, `--include-contradicted`, `--json`.
- **EPIC-044 STORY-044.7:** Optional **`limits.max_entries_per_group`** — per-`memory_group` bucket (plus ungrouped) caps with lowest-confidence eviction inside the bucket; when set, global `max_entries` overflow prefers evicting from the incoming row's group (`StoreHealthReport.max_entries_per_group`, MCP `memory://stats`, native health, CLI `store stats`). See `docs/engineering/data-stores-and-schema.md`.

### Changed

- Version and distribution alignment: Python package, OpenClaw plugin/skill manifests, MCP `server.json`, and SKILL.md bumped to **2.0.4** (no API changes).
- Pre-existing `ruff format` drift cleared in `visual_snapshot.py`, `test_federation.py`, `test_memory_persistence.py`, `test_mcp_server.py`.

### Documentation

- **EPIC-051** (complete): Section 10 checklist decisions as **ADR-001**–**ADR-006** under `docs/planning/adr/` (retrieval, freshness, correctness, scale, SQLCipher ops, save-path observability); cross-links from `docs/engineering/features-and-technologies.md` and `docs/planning/PLANNING.md` (`adr/` in directory tree). **`docs/guides/sqlcipher.md`** — key loss, backup/restore verification, enterprise KMS note (**051.5**).
- EPIC-052 findings notes landed per story in [`docs/planning/epics/EPIC-052.md`](docs/planning/epics/EPIC-052.md) with close-out summary; `persistence.delete_relations` O(n) cleanup path deferred to the open-issues roadmap as a non-blocking optimization candidate.

## v2.0.3 (2026-03-30)

### Changed

- Version and distribution alignment: Python package, OpenClaw plugin/skill manifests, MCP `server.json`, and planning snapshot docs bumped to **2.0.3** (no API changes).

## v2.0.2 (2026-03-29)

### Added

- **Agent integration:** `docs/guides/agent-integration.md`, MCP resource `memory://agent-contract`, `recall_diagnostics` on `memory_recall` / `RecallResult` (empty-reason codes), `StoreHealthReport.package_version` / `profile_name`, `memory://stats` includes package + profile, CLI `tapps-brain memory save`, `scripts/generate_mcp_tool_manifest.py` → `docs/generated/mcp-tools-manifest.json`.
- **Sub-agent memory relay (GitHub #19):** `relay_version` 1.0 schema (`docs/guides/memory-relay.md`), CLI `tapps-brain relay import` (file or `--stdin`), MCP `tapps_brain_relay_export`, rate-limit exempt batch context `memory_relay`. Optional per-item `memory_group` / `group` preserves project-local partitions on import (GitHub #49).
- Adaptive query-aware hybrid search fusion (GitHub **#40**, EPIC-040 **040.10**): `hybrid_rrf_weights_for_query()` and weighted RRF in `MemoryRetriever` when `semantic_enabled=True`. Set `hybrid_config.adaptive_fusion=False` for legacy equal BM25/vector RRF weights.
- Hive batch promotion (GitHub **#18**): CLI `tapps-brain hive push` and `hive push-tagged`; MCP tool `hive_push`; `select_local_entries_for_hive_push` and `push_memory_entries_to_hive` in `hive.py`. `PropagationEngine.propagate` supports `dry_run` and `bypass_profile_hive_rules`; `hive_propagate` accepts `force` and `dry_run`.
- **GC stale listing (GitHub #21):** `MemoryGarbageCollector.stale_candidate_details`, `StaleCandidateDetail`, `MemoryStore.list_gc_stale_details`, CLI `tapps-brain maintenance stale`, MCP `maintenance_stale` (machine-readable reasons for GC candidates).
- **Profile tier migration (GitHub #20):** `tapps_brain.profile_migrate`, `MemoryStore.migrate_entry_tiers`, CLI `tapps-brain profile migrate-tiers --map from:to`, MCP `profile_tier_migrate` (`tier_map_json`, `dry_run`); audit log action `tier_migrate`.

### Changed

- **GC / decay alignment:** CLI `maintenance gc`, MCP `maintenance_gc`, `MemoryStore.gc()`, and `health()` GC candidate counts use profile-derived `DecayConfig` plus store `gc_config` (same rules as `list_gc_stale_details`).
- **OpenClaw auto-capture (tapp-workspace #12):** `extract_durable_facts` recognizes additional phrases common in agent/dev text (`note:`, `summary:`, `we use`, `remember that`, `root cause`, `final approach`, etc.). ContextEngine `ingest` logs `captured=N` from `memory_capture`; `assemble` distinguishes recall-empty vs already-injected. `openclaw.plugin.json` `captureRateLimit` default aligned to `3` (was `5`, inconsistent with plugin runtime default).

## v2.0.1 (2026-03-28)

### Fixed

- **OpenClaw plugin (GitHub #46):** Unwrap MCP `CallToolResult` / structured content when calling recall tools (`mcp_tool_text`, `McpClient.callTool`) so `assemble()` receives memory text.
- **Memory injection:** Include recall `value` in assembled summaries (`inject_memories`).
- **Save tier aliases (GitHub #48):** `tier_normalize.normalize_save_tier` on `MemoryStore.save`, `memory_save` MCP, and relay import; profile layer names matched before global aliases.

### Changed

- **Tool naming (GitHub #47, mitigated):** Plugin registers `tapps_memory_search` / `tapps_memory_get`; host hygiene documented in `docs/guides/openclaw.md`.

### Added

- **Planning:** `docs/planning/design-issue-49-multi-scope-memory.md` for epic #49 (named groups vs Hive namespaces vs profile scopes).

### Chore

- Ruff 0.15.x alignment, `ruff format`, and strict mypy fixes across core and tests.

## v2.0.0 (2026-03-26)

### Research-Driven Upgrades (EPIC-040)

**Algorithm Improvements:**
- BM25+ variant with lower-bound delta for better variable-length scoring (#34)
- FSRS-style adaptive stability — memories that prove useful persist longer (#28)
- Bayesian confidence updates — learn from actual usage patterns (#35)
- Stability-based promotion/demotion strategy (#39)
- Enhanced 6-signal composite scoring with graph centrality and provenance trust (#41)
- TextRank extractive summarization — no LLM required (#32)
- RAKE keyword extraction for automatic key generation (#42)
- Louvain community detection for smarter consolidation (#36)
- PageRank scoring for memory relationship graphs (#33)
- Bloom filter write deduplication (#31)

**Temporal & Provenance:**
- Temporal fact validity windows — valid_from/valid_until (#29)
- Rich provenance metadata — source_session_id, source_channel, triggered_by (#38)
- Per-entry conflict detection and resolution API (#44)

**OpenClaw Plugin:**
- dispose() now flushes conversation context before shutdown (#24)
- Periodic mid-session memory flush every N messages (#25)
- assemble() injects memory recall nudge (#27)
- openclaw init/upgrade CLI commands (#26)

**Multi-Agent:**
- Groups as first-class Hive layer — SESSION → BRAIN → GROUP → HIVE (#37)
- Memory health stats CLI command (#43)

**Schema:** v11 → v15 (4 migrations, all backward-compatible)

---

## [1.4.2] — 2026-03-24

### Changed — Profile limits recalibrated (research-backed)

- **`max_entries` raised from 500 to 5,000** (default) / 10,000 (research-knowledge).
  Old default was the most conservative of any comparable system (Mem0: 10K,
  Obsidian: 10K-12K comfortable, MemGPT/Letta: unbounded). Pure-Python BM25
  at 5K entries runs in ~5-10 ms on desktop, ~15-30 ms on Pi 5. GC and
  auto-consolidation keep the active set well below the limit.
- **`default_token_budget` raised**: repo-brain/customer-support/project-management
  2,000→3,000; personal-assistant 3,000→4,000; research-knowledge 2,000→4,000.
- **Source trust/confidence/ceilings differentiated per profile**:
  customer-support boosts agent trust (0.7→0.8); home-automation boosts system
  trust (0.9→0.95); personal-assistant raises human ceiling (0.95→0.98);
  research-knowledge lowers inferred ceiling (0.70→0.55).
- **GC thresholds differentiated per profile**: personal-assistant/research
  floor 30→60 days; customer-support floor 30→14 days, session 7→3 days;
  home-automation floor 30→7 days; personal-assistant session 7→14 days.
- **Recall thresholds differentiated**: research-knowledge stricter
  (min_score 0.35, min_confidence 0.25); personal-assistant/home-automation
  looser (min_score 0.2).
- **`max_entries` is now profile-aware**: `MemoryStore._max_entries` reads
  from the active profile, falling back to the module default. CLI and MCP
  stats/health endpoints reflect the actual configured limit.
- OpenClaw skill version synced to 1.4.2 (was stale at 1.3.1).

### Added

- `docs/guides/profile-limits-rationale.md` — full research document with
  hardware benchmarks, comparable system analysis, and per-parameter rationale.

---

## [1.4.1] — 2026-03-24

### Fixed

- **F-string SQL hardening in `migration.py`** — added explicit allowlist validation
  for table and column names before f-string interpolation in `PRAGMA table_info` and
  `SELECT` queries. Inputs were already hardcoded tuples (not exploitable), but the
  guards silence static-analysis scanners (Bandit/Semgrep) and protect against future
  maintainer mistakes.
- **Silent exception swallowing** — two `except Exception: pass` blocks now log with
  `exc_info=True`: `store.py` (`decay_config_from_profile` fallback) and
  `diagnostics.py` (`query_feedback` gap count). Failures in these paths were
  previously invisible to debugging.

---

## [1.4.0] — 2026-03-24

### Changed — EPIC-039: Official MCP SDK transport for OpenClaw plugin

- **MCP client rewritten** — replaced 466-line hand-rolled JSON-RPC 2.0 client
  (Content-Length framing, manual stdio parsing, request/response ID matching)
  with the official `@modelcontextprotocol/sdk` (`StdioClientTransport` + `Client`).
  This is the same SDK used by OpenClaw, Claude Desktop, and Cursor.
- **Reconnection model** — exponential-backoff retry loops replaced with
  OpenClaw's session-invalidation pattern (tear down on error, lazy re-create).
- **Stderr logging** — MCP server diagnostic output now piped and logged.
- **Dead process detection** — native `transport.pid` replaces health check timer.
- **No public API change** — `index.ts` required zero modifications.

### Changed — EPIC-037/038: SDK realignment and simplification

- **Plugin SDK types** — ambient `openclaw-sdk.d.ts` replaced with real SDK imports.
- **API contract fixes** — `resolveAgentWorkspaceDir`, `registerTool`,
  `definePluginEntry`, and `registerContextEngine` signatures match real OpenClaw SDK.
- **Dead compat layers removed** — hook-only and tools-only fallback modes removed;
  plugin now requires OpenClaw v2026.3.7+ (`minimumVersion` in manifest).

### Added

- `@modelcontextprotocol/sdk@^1.27.0` as a runtime dependency of the OpenClaw plugin.

---

## [1.3.1] — 2026-03-24

### Added

- **Release gate** — `scripts/release-ready.sh`: packaging build, wheel smoke import, version consistency tests, pytest (optional `SKIP_FULL_PYTEST=1` in CI), ruff, mypy, `openclaw-plugin` npm ci/build/test.
- **OpenClaw docs checker** — `scripts/check_openclaw_docs_consistency.py` (canonical install command, SKILL tool/resource counts vs baseline, runbook presence).
- **CI** — `.github/workflows/ci.yml`: lint runs docs checker; `release-ready` job runs the shell gate after the test matrix.
- **Operator docs** — `docs/guides/openclaw-runbook.md` (canonical PyPI + Git install/upgrade); cross-links from OpenClaw guide, plugin README, skill docs, and `scripts/publish-checklist.md`.

---

## [1.3.0] — 2026-03-23

### Added — EPIC-031: Evaluation & continuous-improvement flywheel

- **Offline evaluation** — BEIR-style loaders, IR metrics, optional LLM judges
  (`evaluation` module, CLI `flywheel evaluate`).
- **Feedback → confidence** — `MemoryStore.process_feedback()`, schema **v11**
  (`positive_feedback_count` / `negative_feedback_count` on entries,
  `flywheel_meta` KV for cursors).
- **Knowledge gaps** — `GapTracker`, zero-result recall signals, optional
  semantic clustering hook; `flywheel gaps` CLI / `flywheel_gaps` MCP tool.
- **Quality reports** — `generate_report`, `ReportRegistry`, `memory://report`
  resource, CLI `flywheel report`.
- **Hive flywheel** — `aggregate_hive_feedback`, `process_hive_feedback`, MCP
  `flywheel_hive_feedback`.
- **MCP / CLI** — `diagnostics_report`, `diagnostics_history`, flywheel tools;
  `tapps-brain-mcp --version`.

### Changed

- **MCP surface** — **54** tools and **7** resources (feedback, diagnostics,
  flywheel, prior graph/audit/Hive coverage).
- **CLI** — **`flywheel`** command group (`process`, `gaps`, `report`,
  `evaluate`, `hive-feedback`).
- **Diagnostics** — recommendations can include flywheel gap summary.

---

## [1.2.0] — 2026-03-22

### Added — EPICs 014–016: Hardening, Analytics & Test Suite

#### Hardening (EPIC-014)
- **`agent_scope` enum validation** — invalid values now return clear errors instead
  of silently defaulting to `private`.
- **CLI `agent create` command** — matches MCP `agent_create` composite tool behavior,
  closing the 3-interface parity gap.
- **SQLite corruption detection** — corrupted databases detected at startup with
  recovery instructions instead of hard crashes.
- **Getting Started guide** (`docs/guides/getting-started.md`) — use-case map with
  quick examples for Library, CLI, and MCP interfaces.
- **CHANGELOG** — release history now tracked in Keep a Changelog format.

#### Analytics & Operational Surface (EPIC-015)
- **Knowledge graph MCP tools + CLI commands** — `memory_relations`,
  `memory_find_related`, `memory_query_relations` exposed via all interfaces.
- **Audit trail queryable** — `memory_audit` MCP tool and `memory audit` CLI command
  for querying the JSONL audit log.
- **Tag management** — `memory_tags`, `memory_tag_update`, `memory_by_tag` tools and
  CLI equivalents for listing, updating, and filtering by tags.
- **Runtime GC configuration** — `maintenance_gc_config` MCP tool and CLI command to
  view/set GC thresholds without restarting.
- **Auto-consolidation config** — `maintenance_consolidation_config` exposed via MCP
  and CLI.
- **Agent lifecycle tools** — `agent_delete`, `agent_list` MCP tools and CLI commands.
- **Hive statistics** — `hive_status` now includes entry counts per namespace.

#### Test Suite Hardening (EPIC-016)
- **CLI federation command tests** — `subscribe`, `unsubscribe`, `publish` now tested.
- **Thread safety verification** — concurrent tests for `MemoryStore`, `HiveStore`,
  metrics, and recall.
- **Resource leak fixes** — eliminated 15 `ResourceWarning: unclosed database`
  warnings across the test suite.
- **Unicode and boundary value tests** — emoji, CJK, RTL, and max key/value length
  boundary tests added.

### Changed
- **MCP tool count** — expanded from 29 to **41 tools** (knowledge graph, audit,
  tags, GC config, consolidation config, agent lifecycle, health, migrate).
- **CLI command count** — expanded from 19 to **36 commands** across 7 groups.
- **Test count** — grew from 1226 to **1683 tests** with 96.48% coverage.

---

## [1.1.0] — 2026-07-15

### Added — EPIC-013: Hive-Aware MCP Surface

- **`--agent-id` and `--enable-hive` MCP server flags** — wire agent identity and
  Hive participation directly from the MCP server CLI. Backward compatible: omitting
  flags preserves current behavior.
- **`agent_scope` parameter in `memory_save`** — callers can now mark memories as
  `private`, `domain`, or `hive` scope directly from MCP. Propagation to the Hive
  DB happens automatically when Hive is enabled.
- **`source_agent` parameter in `memory_save`** — records the originating agent ID
  for every saved memory. Falls back to the server's `--agent-id` when omitted.
- **Shared `HiveStore` instance across Hive MCP tools** — `hive_status`,
  `hive_search`, `hive_propagate`, `agent_register`, and `agent_list` all reuse the
  server's single `HiveStore` instead of creating throwaway instances per call.
- **`hive_propagate` uses server agent identity** — propagation now reads the
  server's resolved agent ID and profile rather than hardcoded defaults.
- **`agent_create` composite MCP tool** — single call to register an agent in
  `AgentRegistry`, validate its profile (built-in or project), and receive a
  namespace assignment with profile summary. Invalid profiles return an error listing
  all available profiles.
- **OpenClaw plugin `agentId` and `hiveEnabled` config fields** — plugin bootstrap
  hook passes `--agent-id` / `--enable-hive` flags to the spawned MCP server and
  auto-calls `agent_register` on first run.
- **Multi-agent Hive patterns in OpenClaw guide** — new section in
  `docs/guides/openclaw.md` covering orchestrator + child agent setup, profile
  inheritance, scope usage, and shared-profile scenarios.
- **Multi-agent Hive round-trip integration tests** — two agents with different
  profiles sharing a Hive: verifies propagation, recall merging, conflict resolution,
  and scope isolation (`private` / `domain` / `hive`).

---

## [1.0.0] — 2026-06-15

### Added — EPICs 001–012: Initial Release

#### Core Memory Engine (EPICs 001–005)
- **Test suite quality** (EPIC-001) — property-based tests, edge-case coverage,
  95 %+ coverage gate enforced by CI.
- **Integration wiring** (EPIC-002) — full end-to-end save → recall → decay →
  consolidation pipeline with real SQLite.
- **Auto-recall orchestrator** (EPIC-003) — token-budgeted ranked memory injection
  into agent prompts; automatic fact extraction from agent responses.
- **Bi-temporal fact versioning** (EPIC-004) — separate `valid_from`/`valid_until`
  (when true) from `recorded_at` (when written). Point-in-time queries and version
  chains. Schema v5.
- **CLI tool** (EPIC-005) — `tapps-brain` command with `save`, `recall`, `list`,
  `delete`, `stats`, and `export` sub-commands via Typer.

#### Retrieval & Quality (EPICs 006–007)
- **Knowledge graph** (EPIC-006) — relation triples (subject → predicate → object)
  stored in SQLite; `add_relation`, `get_relations`, `find_related`, and
  `query_relations` APIs.
- **Observability** (EPIC-007) — `health()` and `get_metrics()` on `MemoryStore`;
  structured JSON audit log (`memory_log.jsonl`); optional OpenTelemetry traces and
  Prometheus-compatible metrics.

#### Distribution (EPICs 008–009)
- **MCP server** (EPIC-008) — 29 MCP tools, 4 resources, 3 prompts. Covers save,
  recall, ingest, sessions, GC, consolidation, validation, knowledge graph, and
  observability.
- **Multi-interface distribution** (EPIC-009) — single engine exposed as Python
  library, CLI, and MCP server. `tapps-brain-mcp` entry-point. PyPI-ready wheel.

#### Profiles & Federation (EPICs 010–011)
- **Configurable memory profiles** (EPIC-010) — YAML-defined profiles with custom
  layers, decay models, scoring weights, and promotion rules. 6 built-in profiles
  (`default`, `coding`, `research`, `creative`, `ops`, `minimal`). YAML inheritance
  via `extends`.
- **Hive — multi-agent shared brain** (EPIC-011) — cross-agent memory sharing via
  `~/.tapps-brain/hive/hive.db`. Namespace isolation, 4 conflict resolution policies
  (`supersede`, `source_authority`, `confidence_max`, `last_write_wins`),
  auto-propagation based on `agent_scope`. Hive-aware recall merges local + Hive
  results with configurable weight.

#### OpenClaw Integration (EPIC-012)
- **Markdown import** — `import_memory_md` and `import_openclaw_workspace` parse
  MEMORY.md and daily notes into tier-classified memories. Idempotent (key-based
  deduplication).
- **OpenClaw ContextEngine plugin** (TypeScript) — `openclaw-plugin/` with
  `bootstrap`, `ingest`, `afterTurn`, and `compact` hooks wired to MCP tools.
- **ClawHub skill** — `openclaw-skill/` with `SKILL.md` manifest and
  `openclaw.plugin.json` for one-click MCP server configuration.
- **PyPI publish preparation** — `project.urls`, wheel + sdist verified,
  publish checklist at `scripts/publish-checklist.md`.

[Unreleased]: https://github.com/wtthornton/tapps-brain/compare/v1.3.1...HEAD
[1.3.1]: https://github.com/wtthornton/tapps-brain/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/wtthornton/tapps-brain/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/wtthornton/tapps-brain/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/wtthornton/tapps-brain/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/wtthornton/tapps-brain/releases/tag/v1.0.0
