# Planning Conventions

This document defines how epics, stories, and tasks are structured in this project so that both humans and AI coding assistants (Claude Code, Cursor, Copilot) can parse, reference, and execute against them consistently.

Feature intake and triage policy for agent-created `feat` work lives in:
- `FEATURE_FEASIBILITY_CRITERIA.md`
- `AGENT_FEATURE_GOVERNANCE.md`
- `ISSUE_TRIAGE_VIEWS.md`

## Open issues roadmap vs Ralph tooling

**Plan (what to follow when):**

| Context | Source of truth for *what* to do next | Packaged in PyPI / OpenClaw? |
|--------|----------------------------------------|-------------------------------|
| Human or Cursor agent implementing shipped features | `open-issues-roadmap.md` (plus epics / GitHub issues) | N/A — this is how delivery is tracked in-repo |
| Ralph autonomous loop (Claude Code CLI) | `.ralph/fix_plan.md` for *that loop’s* next unchecked task | **No** — `.ralph/` is dev automation only, not part of the installable package |

**Fix (avoid drift and wrong edits):**

- Feature PRs and non-Ralph agents should **update `open-issues-roadmap.md`** (and issues) when priorities or status change.
- They should **not** edit `.ralph/` for bookkeeping unless the maintainer explicitly wants Ralph’s checklist synced.
- Ralph is allowed to update `.ralph/fix_plan.md` per `.ralph/PROMPT.md` after its own loops; that does not substitute for updating the roadmap for product tracking.

**Update (ongoing):**

- When starting a Ralph campaign on open-issues work, **copy or reconcile** the OPEN-ISSUES block in `fix_plan.md` from `open-issues-roadmap.md` so Ralph’s queue matches delivery intent.

## Optional backlog gating

**Scope:** Slices **B** (extra save-path observability), **C** (EPIC-042 hygiene), and **NLI / async conflict product wiring** (MCP, worker, or in-app model use — never sync `MemoryStore.save`; offline export is on `main`).

These stay **in the backlog by default** (do not schedule implementation unless a trigger below applies):

- **B — Extra save-path observability** (e.g. EPIC-051.6, metrics beyond `save_phase_summary` on health/MCP).
- **C — EPIC-042 hygiene** (offline eval evidence, GitHub/issue closure against epic success criteria).
- **NLI / async conflict product wiring** (explicit opt-in surface only).

**Work now only if:**

| Trigger | Then consider |
|--------|----------------|
| (a) Actively tuning or **incidenting on save latency** (or consolidation/GC correlation) | **B** — deeper observability |
| (b) A **milestone or stakeholder** requires epic/GitHub closure | **C** — hygiene |
| (c) An **explicit product requirement** for NLI-assisted conflict review | Scope a **separate, opt-in** surface; still **no** silent LLM on sync save |

If none of (a)–(c) apply, **leave these in the backlog** and pick other roadmap or epic work.

## Directory Structure

```
docs/planning/
├── PLANNING.md              ← This file (conventions & templates)
├── open-issues-roadmap.md   ← Canonical GitHub delivery queue (humans / Cursor / releases)
├── STATUS.md                ← Snapshot: schema version, deps, tests, epic vs code (update with releases)
├── adr/                     ← Architecture decision records (e.g. EPIC-051 §10 checklist — ADR-001–006)
└── epics/
    ├── EPIC-001.md      ← Test suite quality — raise to A+ (done)
    ├── EPIC-002.md      ← Integration wiring — connect modules to runtime (done)
    ├── EPIC-003.md      ← Auto-recall — pre-prompt memory injection hook (done)
    ├── EPIC-004.md      ← Bi-temporal fact versioning with validity windows (done)
    ├── EPIC-005.md      ← CLI tool for memory management and operations (done)
    ├── EPIC-006.md      ← Persistent knowledge graph and semantic queries (done)
    ├── EPIC-007.md      ← Observability — metrics, audit trail, health checks (done)
    ├── EPIC-008.md      ← MCP server — expose tapps-brain via MCP (done)
    ├── EPIC-009.md      ← Multi-interface distribution (done)
    ├── EPIC-010.md      ← Configurable memory profiles — pluggable layers and scoring (done)
    ├── EPIC-011.md      ← Hive — multi-agent shared brain with domain namespaces (done)
    ├── EPIC-012.md      ← OpenClaw integration — ContextEngine plugin and ClawHub skill (done)
    ├── EPIC-013.md      ← Hive-aware MCP surface (done)
    ├── EPIC-014.md      ← Hardening — validation, parity, resilience, docs (done)
    ├── EPIC-015.md      ← Analytics & operational surface (done)
    ├── EPIC-016.md      ← Test suite hardening — CLI gaps, concurrency, resource cleanup (done)
    ├── EPIC-017.md      ← Code review — Storage & Data Model (done)
    ├── EPIC-018.md      ← Code review — Retrieval & Scoring (done)
    ├── EPIC-019.md      ← Code review — Memory Lifecycle (done)
    ├── EPIC-020.md      ← Code review — Safety & Validation (done)
    ├── EPIC-021.md      ← Code review — Federation, Hive & Relations (done)
    ├── EPIC-022.md      ← Code review — Interfaces (MCP, CLI, IO) (done)
    ├── EPIC-023.md      ← Code review — Config, Profiles & Observability (done)
    ├── EPIC-024.md      ← Code review — Unit Tests Part 1 (done)
    ├── EPIC-025.md      ← Code review — Integration Tests, Benchmarks & TypeScript (done)
    ├── EPIC-026.md      ← OpenClaw Memory Replacement (done)
    ├── EPIC-027.md      ← OpenClaw Full Feature Surface — MCP tools (done; surface now 54 tools)
    ├── EPIC-028.md      ← OpenClaw Plugin Hardening (done)
    ├── EPIC-029.md      ← Feedback Collection (done)
    ├── EPIC-030.md      ← Diagnostics & Self-Monitoring (done)
    ├── EPIC-031.md      ← Continuous Improvement Flywheel (done)
    ├── EPIC-032.md      ← OTel GenAI Semantic Conventions — standardized telemetry export (planned)
    ├── EPIC-033.md      ← OpenClaw Plugin SDK Alignment (done)
    ├── EPIC-034.md      ← Production readiness QA remediation (done)
    ├── EPIC-035.md      ← OpenClaw install and upgrade UX consistency (done)
    ├── EPIC-036.md      ← Release gate hardening (done; scripts/release-ready.sh, CI)
    ├── EPIC-037.md      ← OpenClaw plugin SDK realignment — fix API contract to match real SDK (done)
    ├── EPIC-038.md      ← OpenClaw plugin simplification — remove dead compat layers (done)
    ├── EPIC-039.md      ← Replace custom MCP client with official @modelcontextprotocol/sdk (done)
    ├── EPIC-040.md      ← tapps-brain v2.0 research-driven upgrades (active; full story checklist in `.ralph/fix_plan.md` § EPIC-040)
    ├── EPIC-041.md      ← Federation hub memory_group (#51), Hive groups (#52), operator clarity (#63–#64)
    └── EPIC-042.md … EPIC-051.md  ← Feature/technology improvement program (`docs/engineering/features-and-technologies.md`; index `epics/EPIC-042-feature-tech-index.md`; **EPIC-051** checklist decisions in `adr/ADR-00*.md`)
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
**Context refs:** `src/tapps_brain/module.py`, `tests/unit/test_memory_module.py`
**Verification:** `pytest tests/unit/test_memory_module.py -v --tb=short -m "not benchmark"`

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
| **Context refs** | Files the AI should read before starting; include **`tests/unit/…` modules that mirror the Verification command** when pytest is the gate |
| **Verification** | Command to run to confirm the story is done; prefer `pytest … -v --tb=short -m "not benchmark"` for unit/integration tests. Use explicit **doc-only** or **design-only** lines when there is no automated gate. **EPIC-042–051** follow the shared rules in [`epics/EPIC-042-feature-tech-index.md`](epics/EPIC-042-feature-tech-index.md). |

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
