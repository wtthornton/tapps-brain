# Deploying tapps-brain to OpenClaw

## Overview

There are **two complementary deployment paths** for getting tapps-brain into OpenClaw:

1. **MCP Server integration** — configure OpenClaw to use our existing `tapps-brain-mcp` stdio server (works today, zero code changes)
2. **ClawHub Skill** — package as a first-class OpenClaw skill and publish to the ClawHub registry for one-command install

Both paths can coexist. The MCP route gives immediate access to all 28 tools. The ClawHub skill provides discoverability, auto-configuration, and a polished install experience for the OpenClaw community.

---

## Path 1: MCP Server (works now)

### What to do

OpenClaw reads MCP server definitions from `~/.openclaw/openclaw.json`. Users add tapps-brain like any other MCP server:

```json
{
  "mcp": {
    "servers": {
      "tapps-brain": {
        "command": "tapps-brain-mcp",
        "args": ["--project-dir", "."],
        "transport": "stdio"
      }
    }
  }
}
```

### Prerequisites for the user

```bash
pip install tapps-brain[mcp]
# or
uv pip install tapps-brain[mcp]
```

### What we should do

- [ ] Add an **OpenClaw** section to `docs/guides/mcp.md` with the config snippet above
- [ ] Test the stdio transport with OpenClaw gateway (verify tool discovery, invocation, resource reads)
- [ ] Document any OpenClaw-specific quirks (e.g., restart-after-config, env var handling)

### Effort: S (documentation only, no code changes)

---

## Path 2: ClawHub Skill (recommended for distribution)

### Why

ClawHub is OpenClaw's skill registry (~3000+ skills, 247k GitHub stars). Publishing there means:
- One-command install: `openclaw skill install tapps-brain-memory`
- Auto-discovery in OpenClaw's skill search
- Automatic MCP server configuration (no manual `openclaw.json` editing)
- Version management and updates via ClawHub

### Step-by-step plan

#### Phase 1: Create the skill directory structure

Create `openclaw-skill/` at the repo root:

```
openclaw-skill/
├── SKILL.md              # Skill metadata + instructions (required)
├── openclaw.plugin.json   # MCP server config auto-injected on install
└── README.md              # ClawHub listing description
```

#### Phase 2: Write SKILL.md

The SKILL.md is the core artifact. It needs YAML frontmatter + markdown instructions:

```yaml
---
name: tapps-brain-memory
version: "1.1.0"               # match pyproject.toml
description: >
  Persistent cross-session memory for AI coding assistants.
  SQLite-backed with BM25 ranking, exponential decay, auto-consolidation,
  and cross-project federation. No LLM calls required.
triggers:
  - remember
  - recall
  - memory
  - forget
  - "what do you remember"
tools:
  - memory_save
  - memory_get
  - memory_search
  - memory_recall
  - memory_reinforce
  - memory_ingest
  - memory_capture
  - memory_list
  - memory_delete
  - memory_supersede
  - memory_history
  - memory_index_session
  - memory_search_sessions
  - memory_export
  - memory_import
  - maintenance_consolidate
  - maintenance_gc
  - federation_status
  - federation_subscribe
  - federation_unsubscribe
  - federation_publish
capabilities:
  - persistent memory across sessions
  - BM25 full-text search with composite scoring
  - auto-recall with token budget control
  - fact extraction from conversations
  - memory decay and consolidation
  - cross-project federation
permissions:
  - filesystem (SQLite database in project directory)
inputs:
  - name: project_dir
    description: Project root for memory storage (defaults to cwd)
    required: false
---
```

The markdown body describes how the agent should use the tools (auto-recall flow, when to save vs. reinforce, etc.).

#### Phase 3: Create openclaw.plugin.json

This file tells OpenClaw how to wire up the MCP server on install:

```json
{
  "name": "tapps-brain-memory",
  "version": "1.1.0",
  "mcp": {
    "servers": {
      "tapps-brain": {
        "command": "tapps-brain-mcp",
        "args": ["--project-dir", "."],
        "transport": "stdio"
      }
    }
  },
  "dependencies": {
    "pip": ["tapps-brain[mcp]>=1.1.0"]
  },
  "postInstall": "pip install tapps-brain[mcp]"
}
```

#### Phase 4: Publish to PyPI (prerequisite)

tapps-brain must be installable via pip for the skill to work:

- [ ] Verify `pyproject.toml` metadata is complete (authors, URLs, license)
- [ ] Add `project.urls` (homepage, repository, documentation)
- [ ] Build: `uv build`
- [ ] Test install from wheel: `pip install dist/tapps_brain-1.1.0-py3-none-any.whl`
- [ ] Publish: `uv publish` (or `twine upload dist/*`)
- [ ] Verify: `pip install tapps-brain[mcp]` works from PyPI

#### Phase 5: Publish to ClawHub

```bash
# Fork github.com/openclaw/clawhub
# Add openclaw-skill/ contents under skills/<your-github-username>/tapps-brain-memory/
# Open PR to the clawhub repo

# Or use the CLI (requires GitHub account ≥1 week old):
openclaw skill publish tapps-brain-memory \
  --slug tapps-brain-memory \
  --name "tapps-brain Memory" \
  --version 1.1.0 \
  --tags latest,memory,mcp,coding-assistant
```

#### Phase 6: Test end-to-end

- [ ] Fresh OpenClaw install
- [ ] `openclaw skill install tapps-brain-memory`
- [ ] Verify MCP server auto-registers in gateway
- [ ] Test: "remember that we use PostgreSQL 16"
- [ ] Test: "what do you remember about the database?"
- [ ] Test: auto-recall triggers on relevant queries
- [ ] Test: federation tools work across projects

---

## Publish checklist

| # | Task | Effort | Blocked by |
|---|------|--------|------------|
| 1 | Add OpenClaw section to `docs/guides/mcp.md` | S | — |
| 2 | Add `project.urls` to `pyproject.toml` | S | — |
| 3 | Create `openclaw-skill/SKILL.md` | M | — |
| 4 | Create `openclaw-skill/openclaw.plugin.json` | S | — |
| 5 | Publish to PyPI | S | #2 |
| 6 | Test MCP server with OpenClaw locally | M | #1 |
| 7 | Submit to ClawHub registry | S | #3, #4, #5 |
| 8 | Test end-to-end install from ClawHub | M | #7 |

## Security considerations

- **ClawHavoc precedent**: In Jan 2026, 824+ malicious skills were found on ClawHub. Our skill must follow best practices:
  - No obfuscated shell commands in SKILL.md
  - No secrets pasted into chat — use env vars
  - Pin dependency versions
  - Clear permission declarations
- tapps-brain's built-in `safety.py` already detects prompt injection patterns
- SQLite database stays local — no network calls unless federation is explicitly configured

## Version sync

Keep these in lockstep:
- `pyproject.toml` → `version`
- `openclaw-skill/SKILL.md` → frontmatter `version`
- `openclaw-skill/openclaw.plugin.json` → `version`
- PyPI release tag

Consider a CI step or pre-commit hook to validate version consistency.

## Open questions

1. **Do we want a Docker-based deployment option?** OpenClaw supports `docker run` for MCP servers. Could provide isolation but adds complexity.
2. **Should auto-recall be opt-in or opt-out?** The `memory_recall` tool is always available, but should the SKILL.md instruct the agent to call it automatically on every message?
3. **PyPI package name**: `tapps-brain` is the current name — verify it's available on PyPI before publishing.
4. **Minimum OpenClaw version**: The MCP plugin interface stabilized in OpenClaw v2026.2.x. Should we declare a minimum version?
