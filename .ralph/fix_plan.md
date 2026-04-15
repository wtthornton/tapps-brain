# Ralph Fix Plan — EPIC-070 AgentForge Integration (Remote-First Brain as a Shared Service)

**Scope:** EPIC-070 — make tapps-brain deployable as a shared networked service consumable by AgentForge workers, Claude Code sessions, and AGENT.md-driven agents via MCP Streamable HTTP.
**Reference:** [EPIC-070](../docs/epics/EPIC-070-agentforge-integration.md) | Stories in `docs/stories/`
**Already done:** STORY-070.1 (Streamable-HTTP MCP transport), STORY-070.2 (service layer), STORY-070.3 (FastAPI HTTP adapter). Start from 070.4.
**Task sizing:** One story per Ralph loop unless marked [BATCH].
**Commits:** Use `feat(story-070.N): description` format.

---

## EPIC-070: Remote-First Brain as a Shared Service




- [ ] **STORY-070.8** — Per-tenant auth tokens (M, 5 pts)
  - `project_profiles` gains `hashed_token` column (migration 011, argon2id)
  - Auth middleware: bearer → hash → project_id; falls back to global token if `TAPPS_BRAIN_PER_TENANT_AUTH` not set
  - CLI: `tapps-brain project rotate-token <slug>` and `revoke-token <slug>`
  - Feature flag: `TAPPS_BRAIN_PER_TENANT_AUTH=1`
  - Integration test: token A cannot recall project B entries
  - Reference: `docs/stories/STORY-070.8-per-tenant-auth.md`

- [ ] **STORY-070.9** — Operator-tool separation (S, 3 pts)
  - Split into `tapps_brain/mcp_server/standard.py` and `tapps_brain/mcp_server/operator.py`
  - Standard: all memory tools. Operator: gc_run, consolidation_merge, consolidation_undo, import, export, migration
  - Two entry points: `tapps-brain-mcp` and `tapps-brain-operator-mcp`
  - Write `docs/guides/agent-md-wiring.md` with safe AGENT.md grant example
  - Reference: `docs/stories/STORY-070.9-operator-tools-split.md`

- [ ] **STORY-070.10** — Native async parity (M, 5 pts)
  - `AsyncMemoryStore` covers every sync `MemoryStore` public method (save, recall, reinforce, hive_search, relay_export, relay_import, consolidate, gc_run, delete, search)
  - Uses `psycopg` async connection pool internally
  - Benchmark: 100-concurrent async recalls ≤ 2× single recall latency
  - Reference: `docs/stories/STORY-070.10-async-parity.md`

- [ ] **STORY-070.11** — Official TappsBrainClient sync + async (L, 8 pts)
  - `tapps_brain.client.TappsBrainClient` and `AsyncTappsBrainClient` with method parity vs `AgentBrain`
  - URL scheme selects transport: `http(s)://` → HTTP adapter; `mcp+stdio://` → subprocess; `mcp+http://` → Streamable-HTTP MCP
  - One `BrainClientProtocol` implemented by three backends; pooled `httpx.AsyncClient` for HTTP
  - Per-call identity, error taxonomy exceptions, idempotency key auto-generated on retry
  - Published in the wheel — no separate package
  - Write `docs/guides/client.md`
  - Reference: `docs/stories/STORY-070.11-tapps-brain-client.md`

- [ ] **STORY-070.12** — OTel + Prometheus label enrichment (S, 3 pts)
  - All memory-op spans carry: `tapps.project_id`, `tapps.agent_id`, `tapps.scope`, `tapps.tool`, `tapps.rows_returned`, `tapps.latency_ms`
  - Prometheus counters and histograms gain `project_id`, `agent_id`, `tool`, `status` labels
  - Label cardinality capped: `agent_id` top-100 per scrape, overflow → `"other"`
  - Add Grafana dashboard JSON at `examples/observability/grafana-per-tenant.json`
  - Reference: `docs/stories/STORY-070.12-otel-prom-labels.md`

- [ ] **STORY-070.13** — AgentForge BrainBridge port — reference implementation (L, 8 pts)
  - Port AgentForge's `BrainBridge` (~925 LOC) to use `TappsBrainClient` in `examples/agentforge_bridge/`
  - Circuit breaker + bounded write queue preserved but thin; target < 250 LOC
  - Tests mirror `test_brain_bridge.py` against a live dockerized brain
  - Does NOT become a runtime dep — lives in `examples/` as documentation
  - Reference: `docs/stories/STORY-070.13-agentforge-bridge-example.md`

- [ ] **STORY-070.14** — Compatibility test suite (S, 3 pts)
  - `tests/compat/test_embedded_3_5_parity.py` pins embedded `AgentBrain` public-method behavior
  - Runs against live Postgres in CI
  - CI job fails PR if any pinned behavior shifts
  - Document policy in `CONTRIBUTING.md`
  - Reference: `docs/stories/STORY-070.14-compat-suite.md`

- [ ] **STORY-070.15** — Docker + docs: one binary, both transports (S, 3 pts)
  - `tapps-brain serve` starts HTTP adapter + Streamable-HTTP MCP on distinct ports in one process
  - Config: `TAPPS_BRAIN_HTTP_PORT` and `TAPPS_BRAIN_MCP_HTTP_PORT`
  - `docker/docker-compose.hive.yaml` updated — single `tapps-brain` service
  - Write `docs/guides/deployment.md` with shared-service pattern, AgentForge client snippet, AGENT.md example
  - Write `docs/guides/migration-3.5-to-3.6.md`
  - Reference: `docs/stories/STORY-070.15-docker-unified.md`
