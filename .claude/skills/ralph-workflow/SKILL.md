---
name: ralph-workflow
description: >
  Ralph's per-loop execution contract — one task from fix_plan.md, the
  RALPH_STATUS exit block, epic-boundary QA deferral, and the dual-condition
  EXIT_SIGNAL gate. Invoke at the start of every Ralph loop so the response
  follows the contract the harness depends on.
version: 1.0.0
ralph: true
ralph_local: true
user-invocable: false
disable-model-invocation: false
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - Task
---

# ralph-workflow — One-Loop Execution Contract

This skill captures the workflow discipline the Ralph harness depends on. The
harness reads your `---RALPH_STATUS---` block after every invocation, tracks
completion indicators, trips the circuit breaker on no-progress loops, and
exits when `EXIT_SIGNAL: true` is paired with ≥2 completion indicators. If
your response skips the status block or misreports a field, Ralph cannot tell
whether work happened — that is what this contract exists to prevent.

## Task source

Ralph reads tasks from one of two backends, set by `RALPH_TASK_SOURCE` in
`.ralphrc`:

- **`file`** (default) — tasks are unchecked `- [ ]` items in
  `.ralph/fix_plan.md`. Tick `- [x]` when done. Empty plan → exit.
- **`linear`** — tasks are open issues in the Linear project named by
  `RALPH_LINEAR_PROJECT`. Pick the highest-priority unblocked issue via
  the Linear MCP (`mcp__plugin_linear_linear__list_issues`), work it,
  comment with what you did, and move it to **Done**. The full state-
  transition rules live in `docs/LINEAR-WORKFLOW.md` when the project
  ships it.

The execution contract below is identical for both backends, with the
substitutions: "fix_plan.md task" ↔ "Linear issue", "tick checkbox" ↔
"move to Done with a comment".

## Execution contract (one loop)

1. **Pick the next task** from the configured backend (see *Task source*
   above) — exactly one. Do not batch unrelated tasks across sections.
2. **Verify the task is still needed.** Re-read the task body /
   acceptance criteria, then search the codebase (Grep/Glob, or
   `ralph-explorer` for anything non-trivial) to confirm the described
   problem still exists and the work is still in scope. If the task is
   **already resolved, moot, or out-of-scope**:
   - **File mode**: tick `- [x]` and append a one-line note
     (`(verified resolved at <commit/file:line>)`).
   - **Linear mode**: post a comment on the issue with evidence (file
     paths, function names, commit hashes), then move it to **Done** via
     the Linear MCP. Do not open a PR.
   - Either way, report `STATUS: COMPLETE`,
     `WORK_TYPE: VERIFICATION`, `TASKS_COMPLETED_THIS_LOOP: 1`,
     `FILES_MODIFIED: 0` (or 1 if you ticked a box),
     `TESTS_STATUS: NOT_RUN`, `EXIT_SIGNAL: false` — and stop. The
     harness will re-invoke for the next task.

   "Trust the plan" is not the same as "skip the read." This step is
   what stops Ralph from grinding on stale tickets. If the codebase is
   too large to search exhaustively, state that in `RECOMMENDATION` and
   proceed to implement — but err toward verifying.
3. Search the codebase before implementing (Grep/Glob, or delegate to
   `ralph-explorer` for anything non-trivial). Prefer existing helpers over
   new abstractions.
4. Implement the smallest change that completes the task. No scope creep, no
   speculative refactors, no "while I'm here" cleanup.
5. Flip the task's checkbox `- [ ]` → `- [x]` in `fix_plan.md` (file mode),
   or move the issue to **Done** with a summary comment (linear mode).
6. Commit the implementation and the fix_plan update together when it makes
   sense as a single logical change.
7. **Decide if this closes the epic.** An epic boundary is the last `- [ ]`
   under a `##` section (file mode), or the last open issue in a Linear
   epic / cycle (linear mode):
   - **Not an epic boundary** → skip QA. Set `TESTS_STATUS: DEFERRED`.
   - **Epic boundary** → run full QA (lint + type + test) for everything in
     the section. If anything fails, fix it before the status block.
7.5. **Deslop pass (epic boundary only).** After QA is green, invoke the
   `simplify` skill on the files changed in this epic. The simplify skill
   removes dead code, unused imports, redundant comments, and speculative
   error handling introduced during the implementation phase — never adds.
   Re-run QA after simplify to confirm nothing regressed. Skip this step if
   `RALPH_NO_DESLOP=true` is set in the environment or `.ralphrc`.
8. Emit the `---RALPH_STATUS---` block (schema below).
9. **STOP.** End your response within 2 lines of `---END_RALPH_STATUS---`.
   Do not start the next task. Do not say "moving on." The harness will
   re-invoke you for the next item.

## The status block

```
---RALPH_STATUS---
STATUS: IN_PROGRESS | COMPLETE | BLOCKED
TASKS_COMPLETED_THIS_LOOP: <number>
FILES_MODIFIED: <number>
TESTS_STATUS: PASSING | FAILING | DEFERRED | NOT_RUN
WORK_TYPE: IMPLEMENTATION | TESTING | DOCUMENTATION | REFACTORING | VERIFICATION
EXIT_SIGNAL: false | true
RECOMMENDATION: <one line, what should happen next>
---END_RALPH_STATUS---
```

Every field is mandatory. `FILES_MODIFIED` counts files the loop actually
touched (not files you read). `RECOMMENDATION` is one line — the harness
surfaces it to the operator as a summary.

## The EXIT_SIGNAL gate

`EXIT_SIGNAL: true` is the hand-off to the harness saying "the plan is done,
stop looping." It requires **all** of the following:

1. Every item in `fix_plan.md` is `[x]` (file mode), **or** the Linear
   project has zero open issues (linear mode).
2. Full QA has run this loop (or a prior loop in this campaign) and is green.
3. No errors/warnings in the last invocation.
4. Every requirement under `specs/` is implemented.
5. Nothing meaningful is left to do.

**Never** pair `EXIT_SIGNAL: true` with `TESTS_STATUS: DEFERRED`. Final exit
requires actual QA, not a mid-epic deferral. The harness combines your
`EXIT_SIGNAL` with NLP completion heuristics (a dual-condition gate) to
avoid shutting down on a stray "done" mid-epic — but that safety net only
works if you are honest about the state.

## Epic-boundary QA rules

- Mid-epic loops **do not** run `npm test` / `bats` / `pytest` / lint. Set
  `TESTS_STATUS: DEFERRED` and stop. Running QA every loop burns budget and
  doesn't surface regressions the epic-boundary run wouldn't also catch.
- At the epic boundary, QA is mandatory for the whole section, not just the
  last task. If it fails, fix before reporting.
- LARGE tasks (cross-module, architectural) run QA in their own scope —
  don't defer on those.

## Scenarios (specification by example)

These are the exact status blocks the harness's circuit breaker and response
analyzer are tuned against. Match the schema; don't invent new field values.

### Making progress, mid-epic (most common)

Task was not the last `- [ ]` in its section.

```
---RALPH_STATUS---
STATUS: IN_PROGRESS
TASKS_COMPLETED_THIS_LOOP: 1
FILES_MODIFIED: 7
TESTS_STATUS: DEFERRED
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: false
RECOMMENDATION: Continue with next task from .ralph/fix_plan.md
---END_RALPH_STATUS---
```

Do **not** spawn `ralph-tester`. The harness reinvokes you for the next item.

### Stale or already-resolved task

You verified at step 2 that the work is already done in the codebase. Mark
the task closed (checkbox or Linear → Done) with evidence; do not write
new code:

```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: 1
FILES_MODIFIED: 1
TESTS_STATUS: NOT_RUN
WORK_TYPE: VERIFICATION
EXIT_SIGNAL: false
RECOMMENDATION: Verified resolved at <commit/file:line> — closed with comment.
---END_RALPH_STATUS---
```

`FILES_MODIFIED: 1` reflects the single fix_plan tick or the Linear
state-change. Use `0` if nothing was written (e.g. you only commented on
the issue without moving it). `EXIT_SIGNAL` stays `false` — the harness
will reinvoke you for the next task.

### Epic boundary reached

Last `- [ ]` in the section; everything in the section is now `[x]`. Run
QA via `ralph-tester`, then run the `simplify` skill on changed files, then
re-run QA to confirm no regression:

```
---RALPH_STATUS---
STATUS: IN_PROGRESS
TASKS_COMPLETED_THIS_LOOP: 1
FILES_MODIFIED: 7
TESTS_STATUS: PASSING
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: false
RECOMMENDATION: Epic complete, QA green, deslop pass done. Next section.
---END_RALPH_STATUS---
```

If QA fails: fix the failures, re-run, then report. Don't ship a red epic.
Skip simplify only if `RALPH_NO_DESLOP=true` is in environment or `.ralphrc`.

### Successful project completion

All `fix_plan.md` items `[x]`, full QA green, specs fully implemented:

```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: 1
FILES_MODIFIED: 1
TESTS_STATUS: PASSING
WORK_TYPE: DOCUMENTATION
EXIT_SIGNAL: true
RECOMMENDATION: All requirements met, project ready for review
---END_RALPH_STATUS---
```

### No work remaining

Plan is empty and nothing meaningful surfaces from `specs/`:

```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: 0
FILES_MODIFIED: 0
TESTS_STATUS: PASSING
WORK_TYPE: DOCUMENTATION
EXIT_SIGNAL: true
RECOMMENDATION: No remaining work, all specs implemented
---END_RALPH_STATUS---
```

### Blocked on external dependency

Task genuinely requires a credential, human decision, or missing upstream
that no reasonable workaround bypasses:

```
---RALPH_STATUS---
STATUS: BLOCKED
TASKS_COMPLETED_THIS_LOOP: 0
FILES_MODIFIED: 0
TESTS_STATUS: NOT_RUN
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: false
RECOMMENDATION: Blocked on [specific dependency] — need [what is needed]
---END_RALPH_STATUS---
```

"I couldn't figure it out" is **not** blocked — that's `IN_PROGRESS` with
the circuit breaker deciding when Ralph has had enough attempts.

### Stuck on a recurring error

Same error in the last ~5 loops, no progress:

```
---RALPH_STATUS---
STATUS: BLOCKED
TASKS_COMPLETED_THIS_LOOP: 0
FILES_MODIFIED: 2
TESTS_STATUS: FAILING
WORK_TYPE: DEBUGGING
EXIT_SIGNAL: false
RECOMMENDATION: Stuck on [error description] — human intervention needed
---END_RALPH_STATUS---
```

## Protected paths — do not touch

Ralph's control surface lives in these paths. Deleting or rewriting them
breaks the loop even if tests pass:

- `.ralph/` (entire directory — state, specs, logs, hooks)
- `.ralphrc` (project config)
- `.claude/agents/ralph*.md` (agent definitions — edit via `ralph-upgrade`)
- `.claude/hooks/on-stop.sh` and `protect-ralph-files.sh` (edit via
  `ralph-upgrade`)

If a cleanup/refactor task seems to require modifying any of these, stop
and re-read the task — almost always the task means code under `src/`, not
the harness itself.

## What not to do

- Don't run tests after every task (see epic-boundary rules).
- Don't skip the deslop pass at epic boundaries unless `RALPH_NO_DESLOP=true`.
- Don't continue with busywork after `EXIT_SIGNAL: true` would be correct.
- Don't refactor code that works. Don't add features outside the plan.
- Don't omit the status block. Without it the harness cannot tell what
  happened and counts the loop as no-progress.
- Don't emit the status block and then keep going. End of response = block.
