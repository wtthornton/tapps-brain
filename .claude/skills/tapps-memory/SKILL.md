---
name: tapps-memory
user-invocable: true
model: claude-sonnet-4-6
description: >-
  Manage shared project memory via tapps-mcp CLI and session notes.
  Use when saving cross-session decisions, searching prior patterns, or
  checking brain bridge health. For chat handoffs use tapps-handoff-session.
allowed-tools: mcp__nlt-code-quality__tapps_session_start mcp__nlt-platform-admin__tapps_session_notes Bash
argument-hint: "[save|search|get] [key]"
---

`tapps_memory` is **not** an MCP tool (removed v3.12.0, TAP-1994). Consumer repos stay **bridge-only** — never add `tapps-brain` to `.mcp.json`.

## Routing guide

| Need | Path |
|------|------|
| Cross-chat handoff | `/tapps-handoff-session` then `/tapps-continue-session` (`.tapps-mcp/session-handoff.md` is canonical) |
| Session-local notes | `mcp__nlt-platform-admin__tapps_session_notes(action="save", ...)` |
| Save / recall / search brain | `uv run tapps-mcp memory <subcommand>` (CLI via BrainBridge) |
| Brain health before writes | `mcp__nlt-code-quality__tapps_session_start()` → `data.brain_bridge_health` |
| Auto-recall at session start | Hooks run `tapps-mcp memory recall` — usually no manual step |

## Shell auth (CLI memory)

CLI reads brain auth from shell env (see `docs/operations/CONSUMER-REPO-BRAIN-WIRING.md`):
- `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN` or `TAPPS_BRAIN_AUTH_TOKEN`
- `TAPPS_MCP_MEMORY_BRAIN_HTTP_URL` or `.tapps-mcp.yaml` → `memory.brain_http_url`

## Decide: should I write to memory?

```
Did the user teach a non-obvious rule?              → YES (save)
Was a decision made WITH RATIONALE that isn't       → YES (architectural / pattern)
  obvious from the code or the PR body?
Did a debug session reveal a subtle invariant?      → YES (pattern, tag: critical)
Is this a TODO / next-step / "remember to do X"?    → NO (use handoff skill or TodoWrite)
Is this re-derivable by reading the repo?           → NO
Does this duplicate a CHANGELOG / CLAUDE.md entry?  → NO
```

## Do NOT save

- Code patterns / file paths / module layout — derivable by reading the repo
- Git history, recent diffs, who-changed-what — `git log` / `git blame` are authoritative
- Ephemeral task state, debug fix recipes — use `tapps_session_notes` or the commit message
- Anything with secrets, tokens, or PII

## Pick a tier (when saving)

| Tier | Half-life | What it's for |
|---|---|---|
| `architectural` | 180d | System decisions, tech-stack choices, infra contracts |
| `pattern` | 60d | Coding conventions, API shapes, design patterns |
| `procedural` | 30d | Workflows, build/deploy commands, runbooks |
| `context` | 14d | Session-scope facts; use sparingly |

Tag important entries with `critical` or `security` via `--tags`.

## CLI commands (daily drivers)

```bash
uv run tapps-mcp memory save --key my-decision --tier architectural --value "..." --tags critical
uv run tapps-mcp memory get --key my-decision
uv run tapps-mcp memory search --query "auth pattern" --json
uv run tapps-mcp memory list --json
uv run tapps-mcp memory export --file memories.json
```

## Advanced surface

Federation, hive, knowledge graph, and batch ops: see `docs/MEMORY_REFERENCE.md`. Ralph coordinator agents may call tapps-brain MCP tools directly; **consumer repo agents use CLI + docs**.

## See also

- `docs/MEMORY_REFERENCE.md` — full legacy action map and brain-health diagnostics
- `docs/operations/CONSUMER-REPO-BRAIN-WIRING.md` — bridge-only checklist and shell auth
