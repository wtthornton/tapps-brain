# Fix Plan — EPIC-070 AgentForge Integration (Remote-First Brain as a Shared Service)

All work is tracked in [EPIC-070](docs/epics/EPIC-070-agentforge-integration.md). Stories reference files in `docs/stories/`. Complete in order — each story gates the next.

Stories 070.1, 070.2, and 070.3 are **already done** (streamable-HTTP MCP transport, service layer, FastAPI HTTP adapter). Start from 070.4.

## Stories

| # | Story | Size | Status |
|---|-------|------|--------|
| 1 | [STORY-070.4 — Error taxonomy + retry-ability semantics](docs/stories/STORY-070.4-error-taxonomy.md) | S (3 pts) | Not started |
| 2 | [STORY-070.5 — Idempotency keys for writes](docs/stories/STORY-070.5-idempotency-keys.md) | S (3 pts) | Not started |
| 3 | [STORY-070.6 — Bulk operations](docs/stories/STORY-070.6-bulk-operations.md) | M (5 pts) | Not started |
| 4 | [STORY-070.7 — Per-call identity (agent_id / scope / group)](docs/stories/STORY-070.7-per-call-identity.md) | M (5 pts) | In Progress |
| 5 | [STORY-070.8 — Per-tenant auth tokens](docs/stories/STORY-070.8-per-tenant-auth.md) | M (5 pts) | Not started |
| 6 | [STORY-070.9 — Operator-tool separation](docs/stories/STORY-070.9-operator-tools-split.md) | S (3 pts) | Not started |
| 7 | [STORY-070.10 — Native async parity](docs/stories/STORY-070.10-async-parity.md) | M (5 pts) | In Progress |
| 8 | [STORY-070.11 — Official TappsBrainClient (sync + async)](docs/stories/STORY-070.11-tapps-brain-client.md) | L (8 pts) | Not started |
| 9 | [STORY-070.12 — OTel + Prometheus label enrichment](docs/stories/STORY-070.12-otel-prom-labels.md) | S (3 pts) | In Progress |
| 10 | [STORY-070.13 — AgentForge BrainBridge port — reference implementation](docs/stories/STORY-070.13-agentforge-bridge-example.md) | L (8 pts) | In Progress |
| 11 | [STORY-070.14 — Compatibility test suite](docs/stories/STORY-070.14-compat-suite.md) | S (3 pts) | In Progress |
| 12 | [STORY-070.15 — Docker + docs: one binary, both transports](docs/stories/STORY-070.15-docker-unified.md) | S (3 pts) | In Progress |
