# Ralph Fix Plan — EPIC-070 AgentForge Integration (Remote-First Brain as a Shared Service)

**Scope:** EPIC-070 — make tapps-brain deployable as a shared networked service consumable by AgentForge workers, Claude Code sessions, and AGENT.md-driven agents via MCP Streamable HTTP.
**Reference:** [EPIC-070](../docs/epics/EPIC-070-agentforge-integration.md) | Stories in `docs/stories/`
**Already done:** STORY-070.1 (Streamable-HTTP MCP transport), STORY-070.2 (service layer), STORY-070.3 (FastAPI HTTP adapter). Start from 070.4.
**Task sizing:** One story per Ralph loop unless marked [BATCH].
**Commits:** Use `feat(story-070.N): description` format.

---

## EPIC-070: Remote-First Brain as a Shared Service











- [ ] **STORY-070.15** — Docker + docs: one binary, both transports (S, 3 pts)
  - `tapps-brain serve` starts HTTP adapter + Streamable-HTTP MCP on distinct ports in one process
  - Config: `TAPPS_BRAIN_HTTP_PORT` and `TAPPS_BRAIN_MCP_HTTP_PORT`
  - `docker/docker-compose.hive.yaml` updated — single `tapps-brain` service
  - Write `docs/guides/deployment.md` with shared-service pattern, AgentForge client snippet, AGENT.md example
  - Write `docs/guides/migration-3.5-to-3.6.md`
  - Reference: `docs/stories/STORY-070.15-docker-unified.md`
