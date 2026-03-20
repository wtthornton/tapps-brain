---
id: EPIC-005
title: "CLI tool for memory management and operations"
status: planned
priority: high
created: 2026-03-19
target_date: 2026-04-30
tags: [cli, operations, ux]
---

# EPIC-005: CLI Tool for Memory Management and Operations

## Context

tapps-brain is currently a Python library only. Managing a memory store requires writing code — there's no way to inspect entries, run searches, manage federation subscriptions, or trigger maintenance (consolidation, GC, migration) from the command line.

This is a barrier to adoption beyond solo developers. Ops teams, data scientists, and AI platform engineers need a CLI to:

- Inspect store contents without writing Python
- Debug retrieval (why did recall return X? what's the decay on Y?)
- Manage federation subscriptions across projects
- Run import/export for backup and migration
- Trigger maintenance operations (consolidation, GC, schema migration)

The project already has `typer` as a dependency pattern (used in the MCP wrapper's tooling). All the underlying operations exist as Python APIs — this epic wires them into a CLI.

## Success Criteria

- [ ] `tapps-brain` CLI entry point installed via `pip install tapps-brain`
- [ ] `tapps-brain store list` / `show` / `search` / `stats` commands for store inspection
- [ ] `tapps-brain memory show <key>` / `history <key>` / `search <query>` for memory operations
- [ ] `tapps-brain import` / `export` commands for JSON and Markdown formats
- [ ] `tapps-brain federation list` / `subscribe` / `unsubscribe` / `publish` commands
- [ ] `tapps-brain maintenance consolidate` / `gc` / `migrate` for store maintenance
- [ ] `tapps-brain recall <message>` for testing auto-recall from the terminal
- [ ] Shell completions for bash/zsh/fish
- [ ] All commands work with `--project-dir` flag (defaults to cwd)
- [ ] Overall coverage stays at 95%+

## Stories

### STORY-005.1: CLI skeleton and project discovery

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `pyproject.toml`, `src/tapps_brain/store.py`
**Verification:** `tapps-brain --help` shows all command groups; `tapps-brain --version` prints version

#### Why

The CLI needs a consistent entry point, argument parsing, and project directory resolution before any commands can be implemented. This story establishes the skeleton that all other stories build on.

#### Acceptance Criteria

- [ ] `src/tapps_brain/cli.py` module with typer-based CLI app
- [ ] Entry point registered in `pyproject.toml` as `[project.scripts] tapps-brain = "tapps_brain.cli:app"`
- [ ] `--project-dir` global option (defaults to cwd, resolves `.tapps-brain/` directory)
- [ ] `--json` global flag for machine-readable output on all commands
- [ ] Shared `get_store()` helper that opens a `MemoryStore` from the resolved project dir
- [ ] `tapps-brain --version` prints package version
- [ ] Unit tests for project directory resolution and store initialization

---

### STORY-005.2: Store inspection commands

**Status:** planned
**Effort:** M
**Depends on:** STORY-005.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_cli.py::TestStoreCommands -v`

#### Why

The most basic CLI need is inspecting what's in the store — how many entries, their tiers, decay states, and search results. This is the "read" side of the CLI.

#### Acceptance Criteria

- [ ] `tapps-brain store stats` — entry count, tier distribution, schema version, store path, federation status
- [ ] `tapps-brain store list` — tabular list of all entries (key, tier, confidence, decay, valid_at/invalid_at)
- [ ] `tapps-brain store list --tier architectural` — filter by tier
- [ ] `tapps-brain store list --include-superseded` — include temporally invalid entries
- [ ] `tapps-brain memory show <key>` — full detail view of a single entry (all fields)
- [ ] `tapps-brain memory history <key>` — version chain display
- [ ] `tapps-brain memory search <query>` — FTS5 search with scored results
- [ ] `tapps-brain memory search <query> --as-of 2026-03-01` — point-in-time search
- [ ] All commands support `--json` output
- [ ] Unit tests for each command with a pre-populated store

---

### STORY-005.3: Import/export commands

**Status:** planned
**Effort:** S
**Depends on:** STORY-005.1
**Context refs:** `src/tapps_brain/io.py`
**Verification:** `pytest tests/unit/test_cli.py::TestImportExport -v`

#### Why

Backup and migration require import/export. The `io.py` module already supports JSON and Markdown — this story wires it into CLI commands.

#### Acceptance Criteria

- [ ] `tapps-brain export --format json > backup.json` — export all entries
- [ ] `tapps-brain export --format markdown > backup.md` — export as Markdown
- [ ] `tapps-brain export --tier architectural --format json` — filtered export
- [ ] `tapps-brain import backup.json` — import from JSON (auto-detects format)
- [ ] `tapps-brain import backup.md` — import from Markdown
- [ ] `tapps-brain import --dry-run backup.json` — preview without writing
- [ ] Unit tests for round-trip export/import

---

### STORY-005.4: Federation management commands

**Status:** planned
**Effort:** M
**Depends on:** STORY-005.1
**Context refs:** `src/tapps_brain/federation.py`
**Verification:** `pytest tests/unit/test_cli.py::TestFederationCommands -v`

#### Why

Federation is configured programmatically today. A CLI lets ops manage cross-project subscriptions, inspect the hub, and debug federation issues without code.

#### Acceptance Criteria

- [ ] `tapps-brain federation status` — show hub path, registered projects, subscription counts
- [ ] `tapps-brain federation list` — list all federated projects
- [ ] `tapps-brain federation subscribe <project-name>` — subscribe to a project's memories
- [ ] `tapps-brain federation unsubscribe <project-name>` — unsubscribe
- [ ] `tapps-brain federation publish` — publish current project's memories to the hub
- [ ] All commands support `--json` output
- [ ] Unit tests for each command

---

### STORY-005.5: Maintenance commands

**Status:** planned
**Effort:** M
**Depends on:** STORY-005.1
**Context refs:** `src/tapps_brain/auto_consolidation.py`, `src/tapps_brain/gc.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_cli.py::TestMaintenanceCommands -v`

#### Why

Store maintenance (consolidation, GC, schema migration) should be runnable from a cron job or CI pipeline without writing Python. This story provides those operational entry points.

#### Acceptance Criteria

- [ ] `tapps-brain maintenance consolidate` — trigger auto-consolidation, report merged entries
- [ ] `tapps-brain maintenance gc` — trigger garbage collection, report archived entries
- [ ] `tapps-brain maintenance gc --dry-run` — preview without archiving
- [ ] `tapps-brain maintenance migrate` — run schema migrations, report before/after version
- [ ] `tapps-brain maintenance migrate --dry-run` — preview migrations
- [ ] `tapps-brain recall <message>` — test auto-recall from terminal, display formatted result
- [ ] Unit tests for each command

---

### STORY-005.6: Shell completions and documentation

**Status:** planned
**Effort:** S
**Depends on:** STORY-005.2, STORY-005.3, STORY-005.4, STORY-005.5
**Context refs:** `docs/guides/`
**Verification:** manual review

#### Why

Shell completions improve CLI usability. Documentation ensures the CLI is discoverable.

#### Acceptance Criteria

- [ ] `tapps-brain --install-completion` installs shell completions (typer built-in)
- [ ] `docs/guides/cli.md` with full command reference, examples, and common workflows
- [ ] README updated with CLI section

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-005.1 — CLI skeleton | M | Foundation: entry point, arg parsing, store resolution |
| 2 | STORY-005.2 — Store inspection | M | Highest-value read operations |
| 3 | STORY-005.3 — Import/export | S | Thin wrapper over existing `io.py` |
| 4 | STORY-005.4 — Federation management | M | Ops need for multi-project setups |
| 5 | STORY-005.5 — Maintenance commands | M | Operational tooling for cron/CI |
| 6 | STORY-005.6 — Completions + docs | S | Polish; depends on all prior stories |

## Dependency Graph

```
005.1 (skeleton) ──┬──→ 005.2 (inspection) ──┐
                   ├──→ 005.3 (import/export) ├──→ 005.6 (completions + docs)
                   ├──→ 005.4 (federation)    │
                   └──→ 005.5 (maintenance) ──┘
```

Stories 005.2–005.5 can be worked in parallel after 005.1 is complete.
