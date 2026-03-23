# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

[Unreleased]: https://github.com/wtthornton/tapps-brain/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/wtthornton/tapps-brain/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/wtthornton/tapps-brain/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/wtthornton/tapps-brain/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/wtthornton/tapps-brain/releases/tag/v1.0.0
