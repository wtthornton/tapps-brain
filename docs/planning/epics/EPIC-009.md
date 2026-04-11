---
id: EPIC-009
title: "Multi-interface distribution — library, CLI, and MCP packaging"
status: done
priority: high
created: 2026-03-20
target_date: 2026-05-31
completed: 2026-03-21
tags: [distribution, packaging, library, cli, mcp]
---

# EPIC-009: Multi-Interface Distribution — Library, CLI, and MCP Packaging

## Context

tapps-brain is becoming a three-interface project: a Python library (`import tapps_brain`), a CLI (`tapps-brain`), and an MCP server (`tapps-brain-mcp`). Each interface targets a different consumer:

- **Library** — Python developers embedding tapps-brain in their own tools
- **CLI** — Ops, data scientists, and developers inspecting/managing stores from the terminal
- **MCP** — AI coding assistants (Claude Code, Cursor, VS Code Copilot, ChatGPT, etc.)

Today the library API exports 91 symbols from `__init__.py`, the CLI is a single `cli.py` module, and MCP is planned in EPIC-008. This epic ensures all three interfaces are cleanly packaged, independently installable via extras, consistently documented, and published to PyPI and the MCP Registry.

Key distribution insight from 2026 research: MCP servers are most commonly distributed via `uvx` (e.g., `uvx tapps-brain-mcp`), with JSON config pointing to the command. The MCP Registry supports PyPI as a package type with a `server.json` manifest.

## Success Criteria

- [x] Library API organized with clear public/internal boundaries
- [x] `pip install tapps-brain` gives the library only (minimal deps)
- [x] `pip install tapps-brain[cli]` adds CLI dependencies (typer)
- [x] `pip install tapps-brain[mcp]` adds MCP server dependencies
- [x] `pip install tapps-brain[all]` installs everything
- [x] `uvx tapps-brain-mcp` works for MCP server distribution
- [x] MCP Registry `server.json` manifest published
- [x] Client config examples for Claude Code, Cursor, VS Code Copilot
- [x] `py.typed` marker for PEP 561 type checking support

## Stories

### STORY-009.1: Dependency extras reorganization

**Status:** done
**Effort:** M
**Depends on:** EPIC-008 STORY-008.1
**Context refs:** `pyproject.toml`, `src/tapps_brain/__init__.py`
**Verification:** `uv sync --group dev && pytest tests/ -v --tb=short`

#### Why

Today `typer` is a core dependency even for library-only consumers. With MCP added, `mcp` would also become a core dep. Both are heavy for users who just want `import tapps_brain`. Reorganizing extras lets each interface pull only its dependencies.

#### Acceptance Criteria

- [x] Core dependencies reduced to: `pydantic`, `structlog`, `pyyaml` (no `typer`, no `mcp`)
- [x] `[cli]` extra adds `typer>=0.24,<1`
- [x] `[mcp]` extra adds `mcp>=1.26.0`
- [x] `[all]` extra combines `cli`, `mcp`, `vector`, `reranker`
- [x] `[dev]` extra still includes everything needed for development
- [x] CLI entry point gracefully errors if `typer` not installed: "Install with `pip install tapps-brain[cli]`"
- [x] MCP entry point gracefully errors if `mcp` not installed: "Install with `pip install tapps-brain[mcp]`"
- [x] All existing tests pass with full extras installed

---

### STORY-009.2: Library API surface cleanup

**Status:** done
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/__init__.py`, `src/tapps_brain/_protocols.py`
**Verification:** `python -c "import tapps_brain; print(tapps_brain.__all__)"` shows organized exports; `mypy --strict src/tapps_brain/`

#### Why

The library exports 91 symbols from `__init__.py`. Library consumers need a clear, stable public API with obvious "start here" entry points. Organizing exports and adding `py.typed` makes tapps-brain a first-class library dependency.

#### Acceptance Criteria

- [x] `__all__` explicitly defined in `__init__.py` with curated public API
- [x] Exports organized into logical groups (core, retrieval, lifecycle, federation, config)
- [x] `py.typed` marker file added for PEP 561
- [x] Internal modules prefixed with `_` are excluded from `__all__`
- [x] No breaking changes — all currently exported symbols remain importable
- [x] Type stubs verified with `mypy --strict`

---

### STORY-009.3: MCP Registry manifest and client configs

**Status:** done
**Effort:** S
**Depends on:** EPIC-008 STORY-008.7
**Context refs:** `pyproject.toml`
**Verification:** manual review — validate `server.json` against MCP Registry schema

#### Why

For tapps-brain to be discoverable in the MCP ecosystem, it needs a registry manifest and ready-to-copy config snippets for popular clients. This is the distribution equivalent of publishing to PyPI.

#### Acceptance Criteria

- [x] `server.json` manifest following `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
- [x] Manifest specifies PyPI package type with `uvx tapps-brain-mcp` as the command
- [x] `docs/guides/mcp.md` includes config snippets for:
  - Claude Code (`.claude/settings.json` or `claude_desktop_config.json`)
  - Cursor (`.cursor/mcp.json`)
  - VS Code Copilot (`mcp.json` in workspace)
  - Generic stdio config
- [x] Config snippets include `--project-dir` usage examples
- [x] README updated with MCP setup section

---

### STORY-009.4: Unified version and metadata

**Status:** done
**Effort:** S
**Depends on:** STORY-009.1
**Context refs:** `pyproject.toml`, `src/tapps_brain/__init__.py`
**Verification:** `tapps-brain --version && tapps-brain-mcp --version` show same version

#### Why

With three interfaces, version consistency matters. The library `__version__`, CLI `--version`, and MCP server info response should all report the same version from a single source of truth.

#### Acceptance Criteria

- [x] Version defined in exactly one place (`pyproject.toml`)
- [x] `tapps_brain.__version__` reads from package metadata (`importlib.metadata`)
- [x] CLI `--version` uses `tapps_brain.__version__`
- [x] MCP server reports version in `initialize` response via `serverInfo`
- [x] Version bump requires changing only `pyproject.toml`

---

### STORY-009.5: CI pipeline for multi-interface testing

**Status:** done
**Effort:** M
**Depends on:** STORY-009.1, STORY-009.2
**Context refs:** `.github/workflows/` (if exists), `pyproject.toml`
**Verification:** CI passes with all test matrix combinations

#### Why

Three interfaces with optional extras create a test matrix. CI must verify that the library works without CLI/MCP deps, that CLI works without MCP deps, and that everything works together. Without this, a change to core could break an interface nobody tested.

#### Acceptance Criteria

- [x] CI test matrix: `[core-only, cli, mcp, all]` extras combinations
- [x] `core-only` run: `pip install .` then `pytest tests/unit/test_memory_store.py tests/unit/test_memory_persistence.py -v`
- [x] `cli` run: `pip install .[cli]` then `pytest tests/ -v` (CLI tests included)
- [x] `mcp` run: `pip install .[mcp]` then `pytest tests/ -v` (MCP tests included)
- [x] `all` run: `pip install .[all,dev]` then full test suite with coverage
- [x] Graceful skip for tests requiring unavailable extras (pytest markers)
- [x] Coverage floor maintained at 95% on `all` run

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-009.2 — API surface cleanup | M | No deps, can start immediately |
| 2 | STORY-009.1 — Extras reorganization | M | Depends on MCP skeleton (008.1) existing |
| 3 | STORY-009.4 — Unified version | S | Quick win after extras are reorganized |
| 4 | STORY-009.5 — CI matrix | M | Validates the extras split works |
| 5 | STORY-009.3 — Registry manifest | S | Last — needs MCP server complete (008.7) |

## Dependency Graph

```
                          EPIC-008 (MCP server)
                                │
009.2 (API cleanup) ──┐        │
                      ├──→ 009.1 (extras reorg) ──→ 009.4 (version) ──→ 009.5 (CI)
                      │
                      └──────────────────────────────────────────────→ 009.3 (registry)
                                                                         │
                                                          EPIC-008.7 ────┘
```

STORY-009.2 has no dependencies and can start immediately. STORY-009.1 requires the MCP server skeleton (008.1) to exist so the `[mcp]` extra can be defined. STORY-009.3 requires the full MCP server (008.7) to be done.
