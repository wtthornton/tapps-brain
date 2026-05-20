# Getting Started with tapps-brain

## Prerequisites

- Python >=3.12
- [uv](https://docs.astral.sh/uv/) (recommended)

## Installation

```bash
uv sync  # or: pip install tapps-brain
```

For development:

```bash
git clone https://github.com/wtthornton/tapps-brain.git
cd tapps-brain
uv sync --all-packages
```

## Project Structure

```
- agent_brain  # Unified Agent API — a simple facade hiding all tapps-brain complexity.
- agent_scope  # Hive ``agent_scope`` normalization (GitHub #52 / EPIC-041 STORY-041.2).
- aio  # Async wrapper for MemoryStore (Issue #66).
- async_postgres_kg  # PostgreSQL async backend for the first-class Knowledge Graph store.
- async_postgres_private  # Async-native PostgreSQL implementation of the PrivateBackend protocol.
- audit  # Audit trail query API (EPIC-007).
- auto_consolidation  # Auto-consolidation triggers for memory subsystem (Epic 58, Story 58.3).
- backends  # Backend factory for Hive and Federation storage (PostgreSQL only).
+ benchmarks/  # End-to-end QA benchmark adapters (STORY-SC01 / TAP-557).
  - locomo  # LoCoMo benchmark adapter (arXiv:2402.17753).
  - longmemeval  # LongMemEval benchmark adapter (arXiv:2410.10813).
- bloom  # Bloom filter for fast approximate membership testing.
- bm25  # BM25 scoring engine for memory retrieval.
+ cli/  # CLI tool for tapps-brain memory management and operations.
  - diagnostics  # ``diagnostics`` and ``flywheel`` sub-app commands (EPIC-030 / EPIC-031).
  - feedback  # ``feedback`` sub-app commands: rate, gap, issue, record, list (EPIC-029).
  - hive  # ``hive`` and ``agent`` sub-apps — Hive status, search, watch, push, and
  - maintenance  # ``maintenance`` sub-app commands.
  - memory  # ``memory`` sub-app commands: save, show, history, relations, related, search,
  - openclaw  # ``openclaw`` sub-app commands: init and upgrade workspace scaffolding.
  - serve  # ``serve`` top-level command plus ``project`` sub-app (EPIC-067, EPIC-069).
  - session  # ``session`` and ``relay`` sub-app commands (Issue #17, GitHub #19).
  - store  # ``store`` sub-app commands: stats, list, groups, search, metrics.
  - top_level  # Top-level CLI commands + the ``profile`` sub-app.
  - visual  # ``visual`` sub-app commands: export JSON snapshot + capture PNG for
- client  # Official TappsBrainClient — sync and async (STORY-070.11).
- consolidation  # Memory consolidation engine (Epic 58, Story 58.2).
- contradictions  # Contradiction detection for memory entries.
- decay  # Time-based decay engine for memory confidence.
- diagnostics  # Quality diagnostics, anomaly detection, and circuit breaker (EPIC-030).
- doc_validation  # Context7-assisted memory validation and enrichment (Epic 62).
- embeddings  # Embedding utilities for semantic search (Epic 65.7).
- errors  # Stable error taxonomy for tapps-brain public APIs (STORY-070.4).
- evaluation  # Offline retrieval evaluation (EPIC-031 STORY-031.3–031.4).
- exceptions  # Client-facing exception taxonomy for the tapps-brain SDK (STORY-071.1).
- experience  # ExperienceEventRecorder — single-transaction atomic write API.
- extraction  # Rule-based extraction of durable facts from session context (Epic 65.5).
- feedback  # Feedback collection data model and storage (EPIC-029).
- flywheel  # Continuous improvement flywheel: feedback → confidence, gaps, reports (EPIC-031).
- fusion  # Reciprocal Rank Fusion (RRF) for hybrid search (Epic 65.8).
- gc  # Garbage collection and archival for memory entries.
- health_check  # Native health check for tapps-brain (issue #15).
+ http/  # tapps-brain HTTP adapter sub-package (TAP-604).
  - auth  # Bearer-token authentication dependencies for the HTTP adapter (TAP-604).
  - metrics_collector  # Prometheus metrics collection for the tapps-brain HTTP adapter (TAP-604).
  - middleware  # ASGI middleware for the tapps-brain HTTP adapter (TAP-604).
  - probe_cache  # Database probe and TTL-caching helpers (TAP-604).
  - profile_resolver  # Singleton ProfileResolver builder for the HTTP adapter (TAP-604).
  - rest_profile_gate  # REST endpoint → MCP tool-name mapping for X-Brain-Profile gating (TAP-1929).
  - settings  # Process-wide settings resolved from environment (TAP-604).
- http_adapter  # FastAPI-based HTTP adapter for tapps-brain (EPIC-070 STORY-070.3/070.4).
- idempotency  # Idempotency key store for HTTP write operations (EPIC-070 STORY-070.5).
- injection  # Memory injection into expert and research responses.
- integrity  # HMAC-SHA256 integrity hashing for memory entries.
- io  # Import and export for shared memory entries.
- kg_query_analysis  # Deterministic entity-mention extraction and KG resolver wiring.
- lexical  # Lexical tokenization for BM25 and FTS5 query building (EPIC-042 STORY-042.1).
- markdown_import  # Markdown import for migrating MEMORY.md files into tapps-brain.
- markdown_sync  # Bidirectional MEMORY.md sync for OpenClaw workspace integration.
+ mcp_server/  # MCP server exposing tapps-brain via Model Context Protocol.
  - context  # Per-request context, store cache, and tool-context dataclass for MCP server.
  - operator  # Operator MCP server — always exposes maintenance/destructive tools.
  - profile_registry  # MCP tool profile registry — EPIC-073 STORY-073.1.
  - profile_resolver  # Per-request MCP profile resolver — EPIC-073 STORY-073.2.
  - server  # FastMCP server skeleton: wires tools, resources, prompts, and CLIs.
  - standard  # Standard MCP server — safe for AGENT.md grants.
  - tool_filter  # Per-request MCP tool filter and authz enforcement — EPIC-073 STORY-073.3.
  - tools_agents  # Agent registry MCP tool registrations.
  - tools_brain  # Agent Brain MCP tool registrations (EPIC-057).
  - tools_feedback  # Feedback, diagnostics, and flywheel MCP tool registrations.
  - tools_hive  # Hive MCP tool registrations (EPIC-011).
  - tools_kg  # Knowledge-Graph MCP tool registrations (EPIC-076 STORY-076.5).
  - tools_maintenance  # Maintenance, health, config, export/import, relay, profile, and session-end MCP tools.
  - tools_memory  # Memory, knowledge-graph, audit, and tag MCP tool registrations.
  - tools_resources  # MCP resource and prompt registrations (read-only store views + workflow templates).
- memory_group  # Project-local memory partition labels (GitHub #49).
- memory_relay  # Structured memory relay format for cross-node / sub-agent handoff (GitHub #19).
- metrics  # In-memory metrics collector for observability (EPIC-007).
+ migrations/
  + federation
  + hive
  + private
  + roles
- models  # Pydantic v2 models for the shared memory subsystem.
- onboarding  # Profile-driven onboarding text for coding agents (GitHub #45).
- openapi_contract  # OpenAPI contract builder for the tapps-brain HTTP adapter (TAP-508).
- otel_exporter  # Optional OpenTelemetry exporter for tapps-brain metrics (STORY-007.5, STORY-061.2).
- otel_tracer  # OpenTelemetry tracing for tapps-brain hot paths (STORY-061.1).
- postgres_connection  # PostgreSQL connection management with pooling for Hive and Federation backends.
- postgres_federation  # PostgreSQL implementation of FederationBackend protocol.
- postgres_hive  # PostgreSQL implementation of HiveBackend and AgentRegistryBackend protocols.
- postgres_kg  # PostgreSQL sync backend for the first-class Knowledge Graph store.
- postgres_migrations  # Migration tooling for PostgreSQL Hive and Federation backends.
- postgres_private  # PostgreSQL implementation of PrivateBackend protocol.
- profile  # Configurable memory profiles — pluggable layers and scoring (EPIC-010).
+ profiles/
- project_registry  # Project profile registry — per-project :class:`MemoryProfile` storage.
- project_resolver  # Transport-layer ``project_id`` resolution.
- promotion  # Promotion and demotion engine for memory entries (EPIC-010).
- rate_limiter  # Sliding window rate limiter for memory write operations.
- recall  # Auto-recall orchestrator for pre-prompt memory injection (EPIC-003).
- recall_diagnostics  # Machine-readable codes for empty recall / injection (agent observability).
- recall_quality_buffer  # Bounded in-process ring buffer for recall-quality telemetry (TAP-2094).
- reinforcement  # Reinforcement system for memory entries.
- relations  # Entity/Relationship extraction from memory entries (Epic 65.12).
- reranker  # Optional reranker for memory retrieval (Epic 65.9).
- retrieval  # Ranked memory retrieval with composite scoring.
- safety  # Content safety - prompt injection detection for stored and retrieved content.
- seeding  # Profile-based memory seeding.
+ services/  # Pure, transport-agnostic service functions for tapps-brain (EPIC-070 STORY-070.1).
  - agents_service  # Agent registry service functions (EPIC-070 STORY-070.1).
  - diagnostics_service  # Diagnostics service functions (EPIC-070 STORY-070.1).
  - feedback_service  # Feedback service functions (EPIC-070 STORY-070.1).
  - flywheel_service  # Flywheel service functions (EPIC-070 STORY-070.1).
  - hive_service  # Hive service functions (EPIC-070 STORY-070.1).
  - kg_service  # Knowledge-Graph service functions for MCP tools and HTTP endpoints.
  - maintenance_service  # Maintenance service functions (EPIC-070 STORY-070.1).
  - memory_service  # Memory-domain service functions (EPIC-070 STORY-070.1).
  - profile_service  # Profile service functions (EPIC-070 STORY-070.1).
  - relay_service  # Relay service functions (EPIC-070 STORY-070.1).
- session_index  # Session indexing for searchable past sessions (EPIC-002, EPIC-065.10).
- session_summary  # Session summarization — episodic memory capture (Issue #17).
- similarity  # Similarity detection for memory consolidation (Epic 58).
- store  # In-memory cache backed by Postgres for the shared memory subsystem.
- tier_normalize  # Normalize memory tier strings from agents, relays, and profiles (GitHub #48).
- visual_snapshot  # Versioned JSON snapshot for brain visual surfaces (dashboard / hero / demos).
- write_policy  # Pluggable write-path policy for MemoryStore (TAP-560 / STORY-SC04).
```

## Key Concepts

- **BrainBridgeCircuitOpenError** - Raised when the circuit breaker is open and a call is rejected.
- **BrainBridge** - AgentForge ↔ tapps-brain bridge using :class:`AsyncTappsBrainClient`.
- **TestCircuitBreaker** - Verify the three-state circuit breaker behaves correctly.
- **TestBoundedWriteQueue** - Verify the bounded write queue drops writes gracefully under backpressure.
- **TestBrainBridgeUnit** - Unit tests for BrainBridge using a mock AsyncTappsBrainClient.
- **TestLocTarget** - Verify brain_bridge.py stays under 250 non-blank, non-comment LOC.
- **TestNotARuntimeDep** - Verify brain_bridge is NOT imported by tapps_brain core modules.
- **TestBrainBridgeIntegration** - Integration tests against a live dockerized tapps-brain.
- **LatencyBucket**
- **FeatureFlags** - Lazy-evaluated feature flags for optional dependencies.

## Running the Project

### `tapps-brain`

```bash
tapps-brain
```

### `tapps-brain-http`

```bash
tapps-brain-http
```


## Running Tests

```bash
pytest
```

## Next Steps

- Read the [Contributing Guide](CONTRIBUTING.md) to learn how to contribute
- Browse the [documentation](docs/) for detailed guides
- See the [Changelog](CHANGELOG.md) for recent changes
