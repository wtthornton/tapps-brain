---
name: tapps-memory
user-invocable: true
model: claude-sonnet-4-6
description: >-
  Manage shared project memory for cross-session knowledge persistence.
  42 actions: save, search, federation, profiles, Hive, knowledge graph, batch ops, feedback, native session memory, and more.
  Use when saving cross-session decisions, searching prior patterns, or managing the project knowledge store.
allowed-tools: mcp__tapps-mcp__tapps_memory mcp__tapps-mcp__tapps_session_notes
argument-hint: "[action] [key]"
---

Manage shared project memory using TappsMCP. **All calls route through `mcp__tapps-mcp__tapps_memory`** — never wire `tapps-brain` directly into `.mcp.json`. The bridge enforces profile filtering, tier rules, feedback-flywheel auto-emission, content-safety gating, and degraded-payload behaviour.

## Decide: should I write to memory?

```
Did the user teach a non-obvious rule?              → YES (feedback)
Was a decision made WITH RATIONALE that isn't       → YES (architectural / pattern)
  obvious from the code or the PR body?
Did a debug session reveal a subtle invariant?      → YES (pattern, tag: critical)
Is this a TODO / next-step / "remember to do X"?    → NO (use TodoWrite)
Is this re-derivable by reading the repo?           → NO
Does this duplicate a CHANGELOG / CLAUDE.md entry?  → NO
```

## Do NOT save

- Code patterns / file paths / module layout — derivable by reading the repo
- Git history, recent diffs, who-changed-what — `git log` / `git blame` are authoritative
- Ephemeral task state, debug fix recipes — these belong in `TodoWrite` or the commit message
- Anything with secrets, tokens, or PII

## Pick a tier (when saving)

| Tier | Half-life | What it's for |
|---|---|---|
| `architectural` | 180d | System decisions, tech-stack choices, infra contracts |
| `pattern` | 60d | Coding conventions, API shapes, design patterns |
| `procedural` | 30d | Workflows, build/deploy commands, runbooks |
| `context` | 14d | Session-scope facts; use sparingly |

Tag important entries with `critical` or `security` for ranking boost.

## Action surface (42 actions)

**Core CRUD:** save, save_bulk, get, list, delete
**Search:** search (ranked BM25 with composite scoring)
**Intelligence:** reinforce (reset decay), gc (archive stale), contradictions (detect stale claims), reseed
**Consolidation:** consolidate (merge related entries with provenance), unconsolidate (undo)
**Import/export:** import (JSON), export (JSON or Markdown)
**Federation:** federate_register, federate_publish, federate_subscribe, federate_sync, federate_search, federate_status
**Maintenance:** index_session (index session notes), validate (check store integrity), maintain (GC + consolidation + contradiction detection)
**Security:** safety_check, verify_integrity | **Profiles:** profile_info, profile_list, profile_switch | **Diagnostics:** health
**Hive / Agent Teams:** hive_status, hive_search, hive_propagate, agent_register
**Knowledge graph (TAP-1630):** related, relations, neighbors, explain_connection
**Batch ops (TAP-1631):** recall_many, reinforce_many
**Feedback flywheel (TAP-1632):** rate (+ auto-emitted feedback_gap on search misses)
**Native session memory (TAP-1633):** index_session, search_sessions, session_end

Steps:
1. Determine the action from the list above
2. For saves, classify tier and scope (project/branch/session/shared) using the tables above
3. Call `mcp__tapps-mcp__tapps_memory` with the action and parameters
4. Display results with confidence scores, tiers, and composite relevance scores
5. For consolidation, use `dry_run=True` first to preview merged entries
6. For federation, register the project first, then publish shared-scope entries

## See also

- [TappsMCP `docs/MEMORY_REFERENCE.md`](https://github.com/wtthornton/TappsMCP/blob/master/docs/MEMORY_REFERENCE.md) — full action reference and brain-health diagnostics
- [tapps-brain `llm-brain-guide.md`](https://github.com/wtthornton/tapps-brain/blob/main/docs/guides/llm-brain-guide.md) — canonical memory model (tiers, when to remember/recall/share, error envelopes)
