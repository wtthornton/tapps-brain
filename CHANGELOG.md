# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- Adaptive query-aware hybrid search fusion (GitHub **#40**, EPIC-040 **040.10**): `hybrid_rrf_weights_for_query()` and weighted RRF in `MemoryRetriever` when `semantic_enabled=True`. Set `hybrid_config.adaptive_fusion=False` for legacy equal BM25/vector RRF weights.
- Hive batch promotion (GitHub **#18**): CLI `tapps-brain hive push` and `hive push-tagged`; MCP tool `hive_push`; `select_local_entries_for_hive_push` and `push_memory_entries_to_hive` in `hive.py`. `PropagationEngine.propagate` supports `dry_run` and `bypass_profile_hive_rules`; `hive_propagate` accepts `force` and `dry_run`.

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

## [Unreleased]

### Added

- **Sub-agent memory relay (GitHub #19):** `relay_version` 1.0 schema (`docs/guides/memory-relay.md`), CLI `tapps-brain relay import` (file or `--stdin`), MCP `tapps_brain_relay_export`, rate-limit exempt batch context `memory_relay`.

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
