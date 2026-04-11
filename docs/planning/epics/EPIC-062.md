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

### STORY-062.1: MCP — Hive backend from unified DSN

**Status:** planned  
**Size:** S  
**Depends on:** EPIC-059 STORY-059.1

#### Why

Wire MCP to `create_hive_backend` with the same env var names as the library (exact string from EPIC-059 STORY-059.7 table).

#### Acceptance criteria

- [ ] `_get_store` (or successor) resolves Hive via `create_hive_backend` from documented env (e.g. `TAPPS_BRAIN_HIVE_DSN` — final name in 059.7).
- [ ] No `HiveStore()` SQLite construction in MCP startup path.
- [ ] Unit test: env set → backend is Postgres class; unset + strict → error path.

#### Verification

- Mocked backend factory tests.

---

### STORY-062.2: MCP — strict vs non-strict startup

**Status:** planned  
**Size:** S  
**Depends on:** STORY-062.1

#### Why

Fail-fast in prod; optional dev ergonomics must be explicit.

#### Acceptance criteria

- [ ] `TAPPS_BRAIN_STRICT=1` (or agreed name): missing DSN → process **exits** with clear, specific message (stderr + exit code non-zero).
- [ ] Non-strict mode documented as **not for production** if retained; or removed if product chooses fail-fast only.

#### Verification

- Integration test: Testcontainers or env matrix.

---

### STORY-062.3: MCP — freeze core tool list

**Status:** planned  
**Size:** M  
**Depends on:** STORY-062.2

#### Why

Agent workflows need a minimal, stable tool set before operator gating.

#### Acceptance criteria

- [ ] **Core** tool list documented in epic PR (bullet list): maps 1:1 to primary agent flows (remember/recall/search/etc.).
- [ ] `docs/generated/mcp-tools-manifest.json` regenerated to match.
- [ ] OpenClaw plugin manifest consistency check if applicable.

#### Verification

- Diff review + manifest JSON diff.

---

### STORY-062.4: MCP — operator tools behind flag

**Status:** planned  
**Size:** M  
**Depends on:** STORY-062.3

#### Why

GC/consolidation must not clutter default agent context.

#### Acceptance criteria

- [ ] **Advanced** / operator tools registered only when `--enable-operator-tools` or profile flag set (exact mechanism in PR).
- [ ] Default MCP session: operator tools absent from capability list.
- [ ] Doc paragraph for operators enabling maintenance tools.

#### Verification

- Test: default vs flag-on tool count.

---

### STORY-062.5: Env contract — single markdown table

**Status:** planned  
**Size:** S  
**Depends on:** EPIC-059 STORY-059.7

#### Why

One table is the handoff artifact for AgentForge and CI.

#### Acceptance criteria

- [ ] One table: variable | meaning | example | required (prod) | required (dev).
- [ ] Includes DSN, strict flag, OTel vars if MCP honors them, agent identity vars.

#### Verification

- Copy-paste dry-run: new contributor sets env from table only.

---

### STORY-062.6: Env contract — links from entrypoints

**Status:** planned  
**Size:** XS  
**Depends on:** STORY-062.5

#### Why

Discoverability: README, AGENTS, agentforge guide must point to the same table.

#### Acceptance criteria

- [ ] Linked from `README.md`, `AGENTS.md`, and `docs/guides/agentforge-integration.md` (anchor or path).
- [ ] `.env.example` at repo root matches table keys (placeholders).

#### Verification

- Link check; grep for drift.

---

### STORY-062.7: CI — docs_validate_epic for v3 epics

**Status:** planned  
**Size:** S  
**Depends on:** —

#### Why

Planning docs drift; gate on changed files.

#### Acceptance criteria

- [ ] CI step runs `docs_validate_epic` when `docs/planning/epics/EPIC-059*.md`–`EPIC-063*.md` change (path filter).
- [ ] Failing validation blocks merge.

#### Verification

- Draft PR with intentional epic typo proves gate.

---

### STORY-062.8: CI — broken-epic regression test

**Status:** planned  
**Size:** XS  
**Depends on:** STORY-062.7

#### Why

Ensure the workflow fails loudly when the tool is misconfigured.

#### Acceptance criteria

- [ ] Documented manual or scripted check: “deliberately break epic frontmatter → CI red” recorded in `docs/contributing` or epic PR template note.
- [ ] Optional: smoke job in `scripts/` that invokes validator on one golden epic.

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
