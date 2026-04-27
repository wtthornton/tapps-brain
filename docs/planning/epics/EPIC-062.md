---
id: EPIC-062
title: "Greenfield v3 — MCP-Primary Integration & Environment Contract"
status: done
priority: high
created: 2026-04-10
updated: 2026-04-27
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

- [x] `tapps-brain-mcp` uses **Postgres** Hive/backend via the same env vars as `AgentBrain`. *(TAPPS_BRAIN_DATABASE_URL / HIVE_DSN)*
- [x] `.env.example` + env table. *(.env.example + docs/guides/postgres-dsn.md)*
- [x] MCP tools grouped: **standard** (`tapps-brain-mcp`) vs **operator** (`tapps-brain-operator-mcp`). *(v3.6.0)*
- [x] CI runs `epic-validation.yml` on v3 epics when touched. *(.github/workflows/epic-validation.yml)*

## Stories

### STORY-062.1: MCP — Hive backend from unified DSN

**Status:** done  
**Size:** S  
**Depends on:** EPIC-059 STORY-059.1

#### Why

Wire MCP to `create_hive_backend` with the same env var names as the library (exact string from EPIC-059 STORY-059.7 table).

#### Acceptance criteria

- [x] MCP resolves Hive via `create_hive_backend` from `TAPPS_BRAIN_DATABASE_URL` / `TAPPS_BRAIN_HIVE_DSN`.
- [x] No SQLite construction in MCP startup path.
- [x] Unit test: env set → Postgres backend; unset + strict → error. *(tests/unit/test_mcp_server.py)*

#### Verification

- Mocked backend factory tests.

---

### STORY-062.2: MCP — strict vs non-strict startup

**Status:** done  
**Size:** S  
**Depends on:** STORY-062.1

#### Why

Fail-fast in prod; optional dev ergonomics must be explicit.

#### Acceptance criteria

- [x] `TAPPS_BRAIN_STRICT=1`: missing DSN → process exits with clear error message.
- [x] Strict mode documented as production default. *(docs/guides/postgres-dsn.md)*

#### Verification

- Integration test: Testcontainers or env matrix.

---

### STORY-062.3: MCP — freeze core tool list

**Status:** done  
**Size:** M  
**Depends on:** STORY-062.2

#### Why

Agent workflows need a minimal, stable tool set before operator gating.

#### Acceptance criteria

- [x] Standard tool list frozen in `mcp_server/standard.py` — only agent-facing tools.
- [x] Operator tools in separate `mcp_server/operator.py` + `tapps-brain-operator-mcp` entrypoint.
- [x] OpenClaw plugin manifest aligned. *(v3.6.0)*

#### Verification

- Diff review + manifest JSON diff.

---

### STORY-062.4: MCP — operator tools behind flag

**Status:** done  
**Size:** M  
**Depends on:** STORY-062.3

#### Why

GC/consolidation must not clutter default agent context.

#### Acceptance criteria

- [x] Operator tools behind separate entrypoint `tapps-brain-operator-mcp`. *(v3.6.0)*
- [x] Default `tapps-brain-mcp` session: operator tools absent.
- [x] Doc paragraph for operators enabling maintenance tools. *(docs/guides/mcp.md)*

#### Verification

- Test: default vs flag-on tool count.

---

### STORY-062.5: Env contract — single markdown table

**Status:** done  
**Size:** S  
**Depends on:** EPIC-059 STORY-059.7

#### Why

One table is the handoff artifact for AgentForge and CI.

#### Acceptance criteria

- [x] One table: variable | meaning | example | required (prod) | required (dev). *(docs/guides/postgres-dsn.md)*
- [x] Includes DSN, strict flag, OTel vars, agent identity vars, pool sizing.

#### Verification

- Copy-paste dry-run: new contributor sets env from table only.

---

### STORY-062.6: Env contract — links from entrypoints

**Status:** done  
**Size:** XS  
**Depends on:** STORY-062.5

#### Why

Discoverability: README, AGENTS, agentforge guide must point to the same table.

#### Acceptance criteria

- [x] Linked from `README.md`, `AGENTS.md`, and `docs/guides/agentforge-integration.md`.
- [x] `.env.example` at repo root exists and matches table keys.

#### Verification

- Link check; grep for drift.

---

### STORY-062.7: CI — docs_validate_epic for v3 epics

**Status:** done  
**Size:** S  
**Depends on:** —

#### Why

Planning docs drift; gate on changed files.

#### Acceptance criteria

- [x] CI step runs `docs_validate_epic` on v3 epics when touched. *(.github/workflows/epic-validation.yml)*
- [x] Failing validation blocks merge.

#### Verification

- Draft PR with intentional epic typo proves gate.

---

### STORY-062.8: CI — broken-epic regression test

**Status:** done  
**Size:** XS  
**Depends on:** STORY-062.7

#### Why

Ensure the workflow fails loudly when the tool is misconfigured.

#### Acceptance criteria

- [x] epic-validation.yml validates YAML frontmatter; intentional breakage → CI red (confirmed).
- [x] Smoke job invoking validator on EPIC-059 as golden epic.

#### Verification

- Maintainer runs script locally.

## Out of scope

- Supporting v2 MCP client configurations.
- Remote MCP OAuth flows (may layer later; not v3.0 blocker if local stdio remains primary).

## References

- `docs/guides/mcp.md`
- `.cursor/mcp.json` patterns in `AGENTS.md`
- [EPIC-059](EPIC-059.md) — Postgres-only persistence (foundation; blocks this epic)
- [EPIC-060](EPIC-060.md) — agent-first API (blocks this epic)
- [EPIC-063](EPIC-063.md) — trust boundaries and MCP endpoint auth/authz
