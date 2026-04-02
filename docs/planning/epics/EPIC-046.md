---
id: EPIC-046
title: "Agent / tool integration — research and upgrades"
status: planned
priority: medium
created: 2026-03-31
tags: [mcp, cli, typer, relay, interoperability]
---

# EPIC-046: Agent / tool integration

## Context

Maps to **§5** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md).

## Success criteria

- [ ] MCP manifest and **OpenClaw** docs stay consistent when tool surface changes (`docs/generated/mcp-tools-manifest.json`).

## Stories

**§5 table order:** **046.1** MCP server → **046.2** CLI (Typer) → **046.3** YAML/JSON relay + profiles.

### STORY-046.1: MCP server (`mcp` SDK)

**Status:** planned | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/mcp_server.py`, `docs/generated/mcp-tools-manifest.json`, `docs/guides/openclaw.md`, `tests/unit/test_mcp_server.py`  
**Verification:** `pytest tests/unit/test_mcp_server.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **MCP** spec evolution: **sampling**, **elicitation**, **tool annotations** — track SDK releases.
- **Structured outputs** (JSON schema) on tools reduce client fragility.

#### Implementation themes

- [ ] **Tool metadata**: deprecations, stability field per tool.
- [ ] **Batch** APIs for multi-save where latency dominates (design review).
- [ ] Rate limit **per tool** telemetry.

---

### STORY-046.2: CLI (Typer)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/cli.py`, `tests/unit/test_cli.py`  
**Verification:** `pytest tests/unit/test_cli.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **Shell completion** and **JSON-only** machine output parity for scripting.
- **Config file** (TOML) for repeated flags vs env only.

#### Implementation themes

- [ ] `tapps-brain --version` / **build metadata** in JSON health.
- [ ] Spike: **single binary** distribution (PyInstaller) — ops ask.

---

### STORY-046.3: Portable interchange (YAML + JSON relay)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/memory_relay.py`, `docs/guides/memory-relay.md`, `profiles/` (YAML profiles), `tests/unit/test_memory_relay.py`  
**Verification:** `pytest tests/unit/test_memory_relay.py -v --tb=short -m "not benchmark"`

#### Research notes (2026-forward)

- **JSON Schema** for relay vNext; **canonical JSON** (sorted keys) for **hash** stability.
- **Compression** (zstd) for large relay blobs in CI/cache.

#### Implementation themes

- [ ] **Forward-compatible** unknown field policy documented.
- [ ] **Validate** relay against schema in CLI before import.

## Priority order

**046.1** → **046.2** → **046.3**.
