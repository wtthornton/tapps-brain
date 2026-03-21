# Planning Conventions

This document defines how epics, stories, and tasks are structured in this project so that both humans and AI coding assistants (Claude Code, Cursor, Copilot) can parse, reference, and execute against them consistently.

## Directory Structure

```
docs/planning/
├── PLANNING.md          ← This file (conventions & templates)
├── STATUS.md            ← Snapshot: schema version, deps, tests, epic vs code (update with releases)
└── epics/
    ├── EPIC-001.md      ← Test suite quality — raise to A+ (done)
    ├── EPIC-002.md      ← Integration wiring — connect modules to runtime (done)
    ├── EPIC-003.md      ← Auto-recall — pre-prompt memory injection hook (done)
    ├── EPIC-004.md      ← Bi-temporal fact versioning with validity windows (done)
    ├── EPIC-005.md      ← CLI tool for memory management and operations (planned)
    ├── EPIC-006.md      ← Persistent knowledge graph and semantic queries (planned)
    ├── EPIC-007.md      ← Observability — metrics, audit trail, health checks (planned)
    ├── EPIC-008.md      ← MCP server — expose tapps-brain via MCP (planned)
    ├── EPIC-009.md      ← Multi-interface distribution (planned)
    ├── EPIC-010.md      ← Configurable memory profiles — pluggable layers and scoring (planned)
    ├── EPIC-011.md      ← Hive — multi-agent shared brain with domain namespaces (planned)
    └── EPIC-012.md      ← OpenClaw integration — ContextEngine plugin and ClawHub skill (planned)
```

## Why This Structure

- **`docs/planning/`** keeps planning artifacts version-controlled alongside code, discoverable via `@docs/planning/` references in AI sessions, and out of the way of source code.
- **One file per epic** keeps each unit of work self-contained. Stories live inside their parent epic file — no need to cross-reference scattered files.
- **YAML frontmatter** on every document lets AI tools parse metadata (status, dependencies, priority) programmatically without reading the full body.

## Epic Format

Every epic file in `docs/planning/epics/` must follow this structure:

```markdown
---
id: EPIC-NNN
title: "Short descriptive title"
status: planned | active | done | abandoned
priority: critical | high | medium | low
created: YYYY-MM-DD
target_date: YYYY-MM-DD
tags: [area1, area2]
---

# EPIC-NNN: Title

## Context
Why this work exists. What problem it solves. Link to any external
references (issues, discussions, incidents).

## Success Criteria
- [ ] Concrete, verifiable outcome 1
- [ ] Concrete, verifiable outcome 2

## Stories

### STORY-NNN.1: Title
(see Story Format below)

### STORY-NNN.2: Title
...

## Priority Order
Table or list showing recommended execution order with rationale.
```

### Frontmatter Fields

| Field | Required | Purpose |
|-------|----------|---------|
| `id` | yes | Unique identifier. Convention: `EPIC-NNN` |
| `title` | yes | Human-readable title, under 70 chars |
| `status` | yes | Current state of the epic |
| `priority` | yes | Business priority |
| `created` | yes | Date created (absolute, never relative) |
| `target_date` | no | Target completion date |
| `tags` | no | Area labels for filtering |

## Story Format

Stories are sections within their parent epic file:

```markdown
### STORY-NNN.S: Title

**Status:** planned | active | done
**Effort:** S | M | L | XL
**Depends on:** STORY-NNN.X (or "none")
**Context refs:** `src/module.py`, `tests/unit/test_module.py`
**Verification:** `pytest tests/unit/test_module.py -v`

#### Why
One or two sentences explaining why this story matters. Not what — why.

#### Acceptance Criteria
- [ ] Specific, testable criterion 1
- [ ] Specific, testable criterion 2
- [ ] Specific, testable criterion 3
```

### Story Field Reference

| Field | Purpose |
|-------|---------|
| **Status** | Current state (matches epic statuses) |
| **Effort** | Relative size: S (<2h), M (2-4h), L (4-8h), XL (8h+) |
| **Depends on** | Blocking dependencies — AI will not start blocked work |
| **Context refs** | Files the AI should read before starting |
| **Verification** | Command to run to confirm the story is done |

## Conventions

### Writing for AI Consumption

1. **Acceptance criteria must be verifiable.** "Improve test quality" is not verifiable. "Module X at 90%+ line coverage" is. AI assistants will use these to determine when work is complete.

2. **Always include a verification command.** If the AI can run `pytest tests/unit/test_foo.py -v` and see green, it knows the story is done. If there's no command, the AI has to guess.

3. **Use absolute dates, never relative.** "Next Thursday" is meaningless in a future session. Use `2026-03-27`.

4. **Context refs are load hints.** When an AI starts a story, it should read the files listed in `Context refs` before writing any code. This prevents blind changes.

5. **Dependencies are ordering constraints.** If Story 3 depends on Story 1, the AI must complete Story 1 first. Keep the dependency graph shallow — deep chains slow everything down.

### Referencing Planning Docs in AI Sessions

In a Claude Code or Cursor session, reference planning docs with:

```
Read @docs/planning/epics/EPIC-001.md and implement STORY-001.3
```

The AI will load the epic context, find the story, read its context refs, implement against the acceptance criteria, and run the verification command.

### Updating Status

- Update story status in the epic file as work progresses.
- When all stories in an epic are `done`, set the epic status to `done`.
- Commit status changes alongside the code they relate to.
- Optionally refresh [`STATUS.md`](./STATUS.md) when schema version, default test counts, or major interface changes land (human-oriented snapshot; Ralph task order stays in `.ralph/fix_plan.md`).

### Commit Messages

Reference story IDs in commit messages using conventional commits:

```
feat(story-001.3): add end-to-end retrieval integration tests
fix(story-001.4): correct eviction ordering assertion
```

This links git history to planning artifacts.

## Anti-Patterns

- **Don't put implementation details in stories.** Stories define *what* and *why*, not *how*. The AI figures out *how* from the code and acceptance criteria.
- **Don't create stories smaller than S.** If it takes less than an hour, it's a task within a story, not its own story.
- **Don't duplicate acceptance criteria across stories.** If two stories share a criterion, one depends on the other.
- **Don't leave verification blank.** Even "manual review required" is better than nothing — it tells the AI to stop and ask.
