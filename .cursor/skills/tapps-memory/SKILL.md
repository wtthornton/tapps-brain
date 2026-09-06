---
name: tapps-memory
description: >-
  Manage shared project memory via tapps-mcp CLI and session notes.
  Use when saving cross-session decisions, searching prior patterns, or
  checking brain bridge health. For chat handoffs use tapps-handoff-session.
mcp_tools:
  - tapps_session_start
  - tapps_session_notes
---
<!-- BEGIN: tapps-skill tapps-memory v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

`tapps_memory` on the **`nlt-memory`** MCP server is a slim facade (TAP-3895). Default consumer path is **`uv run tapps-mcp memory`** (bridge-only — never add direct `tapps-brain` to `.mcp.json`).

## Routing guide

| Need | Path |
|------|------|
| Cross-chat handoff | `tapps-handoff-session` then `tapps-continue-session` |
| Session-local notes | `tapps_session_notes(action="save", ...)` |
| Save / recall / search brain | `uv run tapps-mcp memory <subcommand>` |
| Brain health | `tapps_session_start(quick=false)` → `brain_bridge_health` |

## CLI (daily drivers)

`memory save`, `get`, `search`, `list`, `export` — see skill body for examples. Shell auth: `TAPPS_BRAIN_AUTH_TOKEN` or `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN`.

## Tiers

`architectural` (180d), `pattern` (60d), `procedural` (30d), `context` (14d). Tag with `--tags critical,security` when warranted.

## Advanced

Federation, hive, KG: `docs/MEMORY_REFERENCE.md`. Consumer agents use CLI; coordinator agents may use brain MCP directly.
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 4 heading(s) duplicate the managed block above verbatim (## Routing guide, ## CLI (daily drivers), ## Tiers, ## Advanced); 100% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

`tapps_memory` on the **`nlt-memory`** MCP server is a slim facade (TAP-3895). Default consumer path is **`uv run tapps-mcp memory`** (bridge-only — never add direct `tapps-brain` to `.mcp.json`).

## Routing guide

| Need | Path |
|------|------|
| Cross-chat handoff | `tapps-handoff-session` then `tapps-continue-session` |
| Session-local notes | `tapps_session_notes(action="save", ...)` |
| Save / recall / search brain | `uv run tapps-mcp memory <subcommand>` |
| Brain health | `tapps_session_start(quick=false)` → `brain_bridge_health` |

## CLI (daily drivers)

`memory save`, `get`, `search`, `list`, `export` — see skill body for examples. Shell auth: `TAPPS_BRAIN_AUTH_TOKEN` or `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN`.

## Tiers

`architectural` (180d), `pattern` (60d), `procedural` (30d), `context` (14d). Tag with `--tags critical,security` when warranted.

## Advanced

Federation, hive, KG: `docs/MEMORY_REFERENCE.md`. Consumer agents use CLI; coordinator agents may use brain MCP directly.
