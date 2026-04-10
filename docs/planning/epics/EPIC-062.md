---
id: EPIC-062
title: "Greenfield v3 — MCP-Primary Integration & Environment Contract"
status: planned
priority: high
created: 2026-04-10
tags: [greenfield, mcp, ide, v3, configuration]
depends_on: [EPIC-059, EPIC-060]
blocks: []
---

# EPIC-062: Greenfield v3 — MCP-Primary Integration & Environment Contract

## Goal

Make **MCP** the primary IDE/agent integration, wiring the MCP server to the **same Postgres-backed** Hive and config as `AgentBrain`, and publish a **single env var table** for all hosts.

## Motivation

MCP is the 2026 interoperability layer; divergent defaults (SQLite Hive in MCP vs Postgres in hosts) created split-brain and trust gaps. One contract fixes ops and documentation.

## Context

**MCP is the default integration** for Cursor, Claude Code, and IDE agents. The v3 product must not instantiate a **silent SQLite Hive** while the library uses Postgres—one **environment contract** and one **Hive** (see EPIC-059). CLI remains supported but **secondary** to MCP + library.

## Acceptance Criteria

- [ ] `tapps-brain-mcp` uses **Postgres** Hive/backend via the **same env vars** as `AgentBrain` (no divergent defaults).
- [ ] `.env.example` + README table: every variable, required vs optional, prod vs dev.
- [ ] MCP tools are **grouped**: **agent** (remember/recall/search) vs **operator** (maintenance)—operator tools off by default or behind flag.
- [ ] CI runs **docs_validate_epic** (or equivalent) on `docs/planning/epics/EPIC-059*.md`–`EPIC-063*.md` when touched.

## Stories

### STORY-062.1: MCP server Postgres wiring

**Status:** planned  
**Size:** M  
**Depends on:** EPIC-059 STORY-059.1

#### Why

Today MCP can construct `HiveStore()` (SQLite); that contradicts “one Hive” on Postgres.

#### Acceptance criteria

- [ ] `_get_store` (or successor) builds Hive via `create_hive_backend(os.environ["TAPPS_BRAIN_HIVE_DSN"])` (exact env name per unified contract in STORY-059.3).
- [ ] **Strict mode** env (e.g. `TAPPS_BRAIN_STRICT=1`): missing DSN **exits** at startup with a clear, specific error message.
- [ ] Non-strict dev mode is **opt-in** and documented as **not for production**—or removed entirely if product chooses fail-fast only.

#### Verification

- Integration test: MCP server startup against Testcontainers Postgres.

---

### STORY-062.2: Curated MCP tool surface

**Status:** planned  
**Size:** M  
**Depends on:** STORY-062.1

#### Why

Tool sprawl confuses agents and reviewers; v3 is **agent-first**, not “every internal function exposed.”

#### Acceptance criteria

- [ ] **Core** tool set frozen (list in epic PR); maps 1:1 to primary agent workflows.
- [ ] **Advanced** tools (GC, consolidation sweeps, etc.) behind `profile` or `--enable-operator-tools`.
- [ ] `docs/generated/mcp-tools-manifest.json` (or successor) regenerated and reviewed.

#### Verification

- Diff review + OpenClaw/manifest consistency check if applicable.

---

### STORY-062.3: Cross-tool environment contract doc

**Status:** planned  
**Size:** S  
**Depends on:** STORY-059.3

#### Why

AgentForge, IDE, and CI must share one table of env vars.

#### Acceptance criteria

- [ ] Single markdown table: variable, meaning, example, **required** (prod), **required** (dev).
- [ ] Linked from `README.md`, `AGENTS.md`, and `docs/guides/agentforge-integration.md`.

#### Verification

- Copy-paste test: new contributor sets env from doc only.

---

### STORY-062.4: docs-mcp validation in CI

**Status:** planned  
**Size:** S  
**Depends on:** —

#### Why

Planning docs drift; `docs_validate_epic` already exists on docs-mcp.

#### Acceptance criteria

- [ ] CI step: `docs_validate_epic` on changed epic files under `docs/planning/epics/EPIC-059*.md`–`EPIC-063*.md` (or broader opt-in).
- [ ] Failing validation blocks merge.

#### Verification

- Deliberately broken epic in draft PR proves gate works.

## Out of scope

- Supporting v2 MCP client configurations.
- Remote MCP OAuth flows (may layer later; not v3.0 blocker if local stdio remains primary).

## References

- `docs/guides/mcp.md`
- `.cursor/mcp.json` patterns in `AGENTS.md`
- [EPIC-059](EPIC-059.md) — Postgres-only persistence (foundation; blocks this epic)
- [EPIC-060](EPIC-060.md) — agent-first API (blocks this epic)
- [EPIC-063](EPIC-063.md) — trust boundaries and MCP endpoint auth/authz
