---
name: ralph-workflow
description: >
  Ralph's per-loop execution contract — one task from fix_plan.md, the
  RALPH_STATUS exit block, epic-boundary QA deferral, and the dual-condition
  EXIT_SIGNAL gate. Invoke at the start of every Ralph loop so the response
  follows the contract the harness depends on.
version: 1.2.0
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
  `RALPH_LINEAR_PROJECT`. The state machine is `Backlog/Todo →
  In Progress → Done`. Claude moves the ticket between these states
  in real time as it works. Use the **`linear-read`** skill to discover
  issues — it runs the mandatory `tapps_linear_snapshot_get` cache-first
  dance before any `mcp__plugin_linear_linear__list_issues` call, and
  reuses the cached snapshot for the rest of the loop. Single-issue
  reads (you have the TAP-ID) go straight to
  `mcp__plugin_linear_linear__get_issue` — no skill, no cache. Use
  `mcp__plugin_linear_linear__save_issue` with `state: "In Progress"`
  on pickup, and `state: "Done"` on completion (only after R1 below is
  satisfied). The full state-machine spec lives in
  `docs/LINEAR-WORKFLOW.md`; the must-know rules are inline below.

  **Hard rules (linear mode) — these are not optional**:

  - **R0 — Branch first, never commit on `main`.** Before the first
    `git add` for a ticket, create a feature branch:
    `git checkout -b <branch>` where `<branch>` is the Linear issue's
    `gitBranchName` field (e.g. `tap-2299-content-safety-gate`) or
    `<ticket-id-lowercase>-<short-slug>` if that field is unset. All
    `git commit` / `git push` calls for the ticket happen on that
    branch. **Never `git commit` while `HEAD` is `main`**; **never
    `git push origin main`**. R0 is the *mechanism* by which R1 is
    satisfied — R1 alone (commit-on-main check) accepts direct-to-main
    pushes, which is the loophole that lets a ticket close without ever
    going through review. Before any commit, sanity-check the branch
    with `git rev-parse --abbrev-ref HEAD`; if it returns `main`, stop
    and create the branch. The exception is documentation-only commits
    the operator explicitly authorized (rare; not an autonomous-loop
    default).
  - **R1 — Done requires `main` (via PR).** Before moving a ticket to
    Done, run `git log main --grep='<TICKET-ID>'` and confirm at least
    one matching commit exists on `main` AND that commit's message
    ends with a ` (#NNN)` PR-merge suffix (the GitHub squash-merge
    marker). Absence of the suffix means R0 was bypassed — that is a
    rule violation; report it via `RECOMMENDATION` and leave the ticket
    `In Progress`. If the work is only on a branch, run
    `gh pr create` then `gh pr merge --squash --auto --delete-branch`.
    After a successful squash-merge, also delete the source branch
    locally (`git branch -D <branch>`) and confirm on origin
    (`git push origin --delete <branch>` — best-effort, ignore
    network/permission errors) so the repo stays at `main` + active
    branch. If the merge is blocked (no permission, conflicts,
    required checks pending): post a Linear comment listing the
    unmerged SHAs and **leave the ticket In Progress** — Ralph will
    retry next loop. An unmerged branch is **not** a Done state and is
    **not** an In Review state.

    **R1 exemption — read-only audit work.** R1 does not apply when
    `WORK_TYPE` is `AUDIT` and `FILES_MODIFIED` is 0. Audit work is
    by-design non-mutating — the executor runs analysis tools, files
    findings as child Linear issues, and closes the session ticket.
    No commit on `main` referencing the ticket will exist, and that is
    correct. Detect audit work via either: (a) the ticket carries an
    `audit-readonly` Linear label, or (b) the ticket body contains the
    marker `<!-- ralph: audit-readonly -->` in the first 500
    characters. If neither signal is present, R1 still applies.

    **R1 async-merge mode — `RALPH_ASYNC_MERGE=true` (T5 / 2.16.0,
    opt-in).** When the harness is running with async-merge enabled,
    the per-PR flow changes: open the PR with `gh pr create`, append it
    to `.ralph/pending-merges.json` (the harness provides a small
    helper; see below), and **immediately stop the loop without
    waiting for the merge**. Do NOT call `gh pr merge` yourself. The
    harness polls pending PRs at the next loop boundary; when CI is
    green it merges them. The Linear "Done" transition still happens
    *from this skill* — but on the loop AFTER the merge actually
    lands. At each loop start, the harness will surface a
    `PENDING LINEAR CLEANUP: TAP-N1,TAP-N2,...` line in your context
    listing tickets whose PR is on `main` but whose Linear state is
    still `In Progress`. Move each one to Done via the `linear-issue`
    skill, then drop the entry by deleting it from
    `.ralph/pending-merges.json`. The harness will also surface
    `PENDING-MERGE FAILURES: ...` for any PR whose CI failed — fix the
    cause, re-open the PR, and the queue picks it up.

    To append to the queue, source `lib/pending_merges.sh` from a Bash
    snippet and call `pending_merges_add <pr_number> <ticket_id>
    <branch_name>` — the helper is idempotent and bounded by
    `RALPH_ASYNC_MERGE_MAX_PENDING` (default 5). If the queue is at
    cap (`pending_merges_add` returns 2), the helper signals "force
    drain" — at that point fall back to the synchronous flow for the
    current ticket so the queue can recover.

    When `RALPH_ASYNC_MERGE` is false (the default), use the standard
    synchronous flow above.
  - **R2 — In Review is for hard blockers only.** Use it only when work
    cannot proceed AND the blocker matches one of: missing credentials
    Claude cannot generate, explicit budget/spend cap reached,
    irreversible destructive operation requiring human sign-off, or
    genuinely ambiguous product decision with no safe default. "Needs
    code review", "couldn't figure it out", "unmerged branch", and
    flaky tests are **not** In Review reasons. When in doubt: pick
    Done if AC is substantively met, In Progress otherwise.
  - **R3 — Retry In Progress before picking new.** The harness injects
    the highest-priority In Progress ticket assigned to Ralph as
    `RESUME IN PROGRESS` in your context. Resolve it (usually:
    self-merge its branch) before picking a new ticket from the
    Backlog/Todo queue.
  - **R4/R5 — Hands off Backlog, Canceled, Duplicate.** Those are
    human triage states. If you think a ticket should be canceled or
    duplicates another, post a comment recommending it and leave the
    state alone.

The execution contract below is identical for both backends, with the
substitutions: "fix_plan.md task" ↔ "Linear issue", "tick checkbox" ↔
"move to Done with a comment".

### Linear writes — delegation pattern

The main Ralph agent's `tools:` list intentionally omits
`mcp__plugin_linear_linear__*` so the boundary in
`.claude/rules/agent-scope.md` stays enforceable — Ralph cannot
accidentally mutate a sibling project's Linear workspace from inside a
loop. When the workflow requires a Linear write (state transition,
comment, child-issue filing, label change), delegate via `Task`:

1. Route through the **`linear-issue`** skill (epic/story
   create/update) or **`linear-read`** skill (multi-issue list). Both
   skills enforce the docs-mcp validator → `save_issue` chain that
   `.claude/hooks/tapps-pre-linear-write.sh` requires (a
   `docs_validate_linear_issue` sentinel < 30 min old). Raw
   `save_issue` calls without that sentinel are blocked.
2. Spawn a subagent that DOES carry the Linear MCP tools — typically
   `general-purpose` (or `claude-agent` where available):

   ```
   Task(general-purpose, "Using the linear-issue skill, move TAP-NNNN to Done with summary comment: <one-paragraph summary>. Project: <project name>. Then call tapps_linear_snapshot_invalidate.")
   ```

3. Single-issue **reads** (you have the TAP-ID) go straight to
   `mcp__plugin_linear_linear__get_issue` via the same subagent — no
   skill wrapping needed (per `linear-standards.md`).
4. For audit-session work (see "Read-only audit task" scenario
   below): the close step files child findings + posts the summary
   comment + moves to Done. Batch the whole close into one subagent
   invocation so the docs-mcp sentinel covers every write.

Never attempt `mcp__plugin_linear_linear__save_issue` from the main
agent directly — the call will be denied by the tool list. If you
catch yourself reaching for it, that's the signal to spawn a `Task`.

## Execution contract (one loop)

0. **(Linear mode only) Honor optimizer hint.** If `LOCALITY HINT: <ID>`
   appears in your context, and that issue is still Backlog/Todo/In-Progress,
   work it instead of running normal priority selection. After picking it up,
   delete the hint file: `rm -f .ralph/.linear_next_issue`. If the issue is
   Done/Cancelled, the hint file is missing, or the ID looks malformed, skip
   this step and use step 1 normally.

1. **Pick the next task** from the configured backend (see *Task source*
   above) — exactly one. Do not batch unrelated tasks across sections.
   In **linear mode**: first check for a `RESUME IN PROGRESS` ticket
   injected into your context (R3) and finish it before picking a new
   one; then move the picked ticket to **In Progress** via
   `mcp__plugin_linear_linear__save_issue` with `state: "In Progress"`
   *before* doing any work, so the Linear board reflects what's
   actually being worked on right now.
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
     `FILES_MODIFIED: 0` (file mode: 1 if you ticked a checkbox; linear
     mode: 0 — comments and state transitions don't write to disk),
     `TESTS_STATUS: NOT_RUN`, `EXIT_SIGNAL: false` — and stop. The
     harness will re-invoke for the next task.

   "Trust the plan" is not the same as "skip the read." This step is
   what stops Ralph from grinding on stale tickets. If the codebase is
   too large to search exhaustively, state that in `RECOMMENDATION` and
   proceed to implement — but err toward verifying.
3. Search the codebase before implementing (Grep/Glob, or delegate to
   `ralph-explorer` for anything non-trivial). Prefer existing helpers over
   new abstractions.
3.5. **Create the feature branch (R0).** Before any `Edit` / `Write` that
   produces a commit, run `git rev-parse --abbrev-ref HEAD`. If it returns
   `main`, run `git checkout -b <branch>` where `<branch>` is the Linear
   issue's `gitBranchName` (or `<ticket-id-lowercase>-<short-slug>` if
   unset). All subsequent commits this loop happen on that branch. R0 is
   not optional and not gated to "complex" work — single-file fixes go
   through a branch + PR the same as multi-file refactors.
4. Implement the smallest change that completes the task. No scope creep, no
   speculative refactors, no "while I'm here" cleanup.
5. Close the task. **File mode**: flip the checkbox `- [ ]` → `- [x]`
   in `fix_plan.md`. **Linear mode**: if `WORK_TYPE` is `AUDIT` and the
   ticket has the `audit-readonly` signal, skip the R1 `git log` check
   entirely and proceed to close — post a summary comment listing the
   findings (TAP-#### children filed) and move the ticket to **Done**.
   Otherwise satisfy R1 first — run
   `git log main --grep='<TICKET-ID>'` to confirm at least one commit
   is on `main`; if the work is branch-only, attempt self-merge; if
   the merge is blocked, post a comment with the unmerged SHAs and
   **leave the ticket In Progress** for retry next loop. Only when R1
   is satisfied (or R1-exempt audit work): post a summary comment and
   move the ticket to **Done** via `save_issue` with `state: "Done"`.
6. Commit the implementation and the fix_plan update together when it makes
   sense as a single logical change.
7. **Decide if this closes the epic.** An epic boundary is the last `- [ ]`
   under a `##` section (file mode), or the last open issue in a Linear
   epic / cycle (linear mode):
   - **Not an epic boundary** → skip QA. Set `TESTS_STATUS: DEFERRED`.
   - **Epic boundary** → run full QA (lint + type + test) for everything in
     the section, in **parallel fan-out** (TAP-1684 — see "Parallel QA
     fan-out" below). If anything fails, fix it before the status block.

   **Parallel QA fan-out at the epic boundary.** Dispatch
   `ralph-tester`, `ralph-reviewer`, and `tapps-validator` in **one
   message with three `Task` tool calls**, not serially. Claude Code's
   `Task` tool runs sibling calls concurrently; serial dispatch waits
   for the slowest agent at every step and inflates epic-boundary
   wall-clock by the sum of all three (typical 4–7 min) when it could
   cost the slowest one (typical 3–5 min). Worked example — note the
   three calls in a single assistant message:

   ```
   <message>
   Task(ralph-tester,   "Run full QA for the <epic-name> section: pytest, ruff, mypy on changed files. Report PASS/FAIL with a one-line summary.")
   Task(ralph-reviewer, "Review the diff for the <epic-name> section against acceptance criteria. Report PASS/FAIL with the one issue that blocks PASS, if any.")
   Task(tapps-validator,"Validate quality gates on changed files in the <epic-name> section via tapps_validate_changed. Report PASS/FAIL.")
   </message>
   ```

   **Aggregation rule (TAP-1684).** A single FAIL or TIMEOUT from any
   of the three collapses the gate to FAIL — the same semantics serial
   dispatch had via early-exit. Name the failing agent in your follow-
   up commit / RECOMMENDATION so the operator can act on the right
   signal. **Order of results in the assistant's response is not
   significant** — wait for all three to return before deciding. The
   helper `exec_aggregate_qa_results` (`lib/exec_helpers.sh`) implements
   the same rule for any harness-side aggregation; the agent surface
   here applies the same logic in prose.
7.5. **Deslop pass (epic boundary only).** After QA is green, invoke the
   `simplify` skill on the files changed in this epic. The simplify skill
   removes dead code, unused imports, redundant comments, and speculative
   error handling introduced during the implementation phase — never adds.
   Re-run QA after simplify to confirm nothing regressed. Skip this step if
   `RALPH_NO_DESLOP=true` is set in the environment or `.ralphrc`.
8. **Verify R0 was honored.** If you committed anything this loop, run
   `git log -1 --format='%H %s' main` (after any merge) — if your
   `TAP-####` ID appears in the message but the message lacks a
   ` (#NNN)` PR-merge suffix, R0 was bypassed (the commit went direct
   to main). Report it in `RECOMMENDATION` and pivot to revert + redo
   via PR next loop. The AgentForge 2026-05-21 campaign had 13 of 20
   commits go direct-to-main because step 5 didn't check; this is the
   harness-side check that should have caught it.
9. **Emit the `---RALPH_STATUS---` block (schema below).** This is
   non-negotiable. Emit the block on **every** loop:
   - Productive loop → fill the fields honestly.
   - No-op loop (nothing to do, queue empty, all blocked) → emit
     `STATUS: COMPLETE` (or `BLOCKED`) + `EXIT_SIGNAL: true` with the
     appropriate Grounds (see the EXIT_SIGNAL gate).
   - Early-exit / coordinator BLOCK / hook denial / API error → emit
     `STATUS: BLOCKED`, `TASKS_COMPLETED_THIS_LOOP: 0`,
     `FILES_MODIFIED: 0`, `EXIT_SIGNAL: false`, and put the reason in
     `RECOMMENDATION`.

   Three consecutive missing blocks trip the harness halt detector
   (`no_status_block_3x`) and stop the campaign. The TAP-1899
   productivity guard resets the counter when `files_modified>=1 OR
   tasks_done>=1`, but a truly no-op loop without a block will halt.
   When in doubt, emit anyway — the wrong block is recoverable; no
   block kills the campaign.
10. **STOP.** End your response within 2 lines of `---END_RALPH_STATUS---`.
    Do not start the next task. Do not say "moving on." The harness will
    re-invoke you for the next item.

## The status block

```
---RALPH_STATUS---
STATUS: IN_PROGRESS | COMPLETE | BLOCKED
TASKS_COMPLETED_THIS_LOOP: <number>
FILES_MODIFIED: <number>
TESTS_STATUS: PASSING | FAILING | DEFERRED | NOT_RUN
WORK_TYPE: IMPLEMENTATION | TESTING | DOCUMENTATION | REFACTORING | VERIFICATION | AUDIT
EXIT_SIGNAL: false | true
RECOMMENDATION: <one line, what should happen next>
---END_RALPH_STATUS---
```

Every field is mandatory. `FILES_MODIFIED` counts files the loop actually
touched (not files you read). `RECOMMENDATION` is one line — the harness
surfaces it to the operator as a summary.

**Linear-mode additions** (when `RALPH_TASK_SOURCE=linear`): also include
these so the live monitor can show what you're working on and the harness
can detect plan_complete:

```
LINEAR_ISSUE: <ID-or-NONE>      (e.g. TAP-915, or NONE if no issue touched)
LINEAR_OPEN_COUNT: <N>          (open issues in the project, via Linear MCP)
LINEAR_DONE_COUNT: <N>          (completed issues, via Linear MCP)
LINEAR_EPIC: <ID>               (optional — only if working under an epic)
LINEAR_EPIC_DONE: <N>           (optional — stories Done in the epic)
LINEAR_EPIC_TOTAL: <N>          (optional — total stories in the epic)
NEXT_INTENDED_ISSUE: <ID-or-NONE>  (T4 / 2.15.9 — see below)
```

### T4 / 2.15.9 — `NEXT_INTENDED_ISSUE` (optional lookahead hint)

Emit `NEXT_INTENDED_ISSUE: TAP-NNNN` when you have already decided which
ticket you will pick on the next loop (e.g. the next story in an epic you
are working through, or the next ticket the locality optimizer should
prefer). The harness then pre-warms `.ralph/brief-next.json` in the
background for that ticket, so the next loop's coordinator can skip its
own spawn (saves ~5–15 s on the next loop's cold start).

- If you don't know what's next, emit `NEXT_INTENDED_ISSUE: NONE` or omit the field — both are no-ops.
- The pre-warmed brief is consumed only when its `task_id` matches the next loop's `.next_intended_issue`. A mismatch is silently dropped, so a wrong guess costs nothing beyond one wasted Haiku call.
- If a `brief.json` arrives with a `task_id` that doesn't match the ticket you actually picked, ignore the brief — Linear is the source of truth.

## The EXIT_SIGNAL gate

`EXIT_SIGNAL: true` is the hand-off to the harness saying "stop looping —
either the plan is done, or there is nothing actionable to do right now."

There are two valid grounds for emitting it:

**Grounds 1 — plan complete** (paired with `STATUS: COMPLETE`). Requires
**all** of the following:

1. Every item in `fix_plan.md` is `[x]` (file mode), **or** the Linear
   project has zero open issues (linear mode).
2. Full QA has run this loop (or a prior loop in this campaign) and is green.
3. No errors/warnings in the last invocation.
4. Every requirement under `specs/` is implemented.
5. Nothing meaningful is left to do.

**Grounds 2 — queue fully blocked** (paired with `STATUS: BLOCKED`).
Requires **all** of the following:

1. Every open item in the queue (file mode: every unchecked `- [ ]`;
   linear mode: every Backlog/Todo/In-Progress issue) is blocked on
   external action you cannot resolve in this loop — credentials you
   cannot generate, upstream systems not yet ready, human decisions,
   `blocked:foo` labels, etc.
2. You actually checked — listed the queue and assessed each item, not
   just the one task you happened to pick.
3. No path exists to make any of them actionable through reasonable
   workarounds (different approach, smaller scope, parallel task).

In Grounds 2, `RECOMMENDATION` must summarize what needs to unblock and
roughly when to retry. The harness treats this as a clean exit (no
circuit-breaker increment) — you save it from grinding on a fully blocked
queue and tripping the no-progress breaker on what is actually a correct
"nothing to do right now" state.

"I couldn't figure out this one task" is **not** Grounds 2 — that is
`STATUS: IN_PROGRESS` with the circuit breaker deciding when Ralph has had
enough attempts. Grounds 2 is a queue-wide assessment, not a per-task
escape hatch.

**Never** pair `EXIT_SIGNAL: true` with `TESTS_STATUS: DEFERRED` (under
Grounds 1 — final completion exit requires actual QA, not a mid-epic
deferral). Under Grounds 2, `TESTS_STATUS: NOT_RUN` is correct because
no work happened.

The harness combines your `EXIT_SIGNAL` with NLP completion heuristics
(a dual-condition gate) to avoid shutting down on a stray "done" mid-epic
— but that safety net only works if you are honest about the state.

## Epic-boundary QA rules

- Mid-epic loops **do not** run `npm test` / `bats` / `pytest` / lint. Set
  `TESTS_STATUS: DEFERRED` and stop. Running QA every loop burns budget and
  doesn't surface regressions the epic-boundary run wouldn't also catch.
- At the epic boundary, QA is mandatory for the whole section, not just the
  last task. If it fails, fix before reporting.
- LARGE tasks (cross-module, architectural) run QA in their own scope —
  don't defer on those.

## Sub-agent fan-out rules (T3 / 2.15.8)

Every sub-agent spawn costs ~10–30 s of orchestration. Most productive
loops should use ≤4 sub-agents; the monitor soft-warns above an avg of 5.
The agent contract (`.claude/agents/ralph.md`) carries the full list, but
the bright lines are:

- **Don't spawn for single-`Bash` ops** — `gh pr merge --squash`,
  `git push`, `git branch -D`, single-file `cat` are all one-line calls.
- **Don't spawn for Linear writes** — the `linear-issue` skill already
  does the dance from a sub-agent at the call site that needs it.
- **Don't spawn `ralph-explorer` when `brief.json` already names the
  files** — `affected_modules` IS the exploration result.
- **Don't spawn for a single Read or Grep.** Sub-agents amortize work
  over a context window — one read in a fresh window is pure spinup cost.

Legitimate spawns:
- Worktree-isolated work (ralph-tester, tapps-review-fixer)
- Multi-file fan-out searches
- **Epic-boundary QA fan-out (3 agents in ONE message)** —
  ralph-tester + ralph-reviewer + tapps-validator dispatched together
  via three `Task` calls in a single message. Aggregation rule:
  **any FAIL or TIMEOUT collapses to FAIL** (matches the serial early-
  exit semantics). The `.subagent_in_flight` sidecar coordinates so
  no CB update lands while >1 agent is outstanding.
- LARGE-task delegation to `ralph-architect`

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
FILES_MODIFIED: 0
TESTS_STATUS: NOT_RUN
WORK_TYPE: VERIFICATION
EXIT_SIGNAL: false
RECOMMENDATION: Verified resolved at <commit/file:line> — closed with comment.
---END_RALPH_STATUS---
```

`FILES_MODIFIED` counts files written to disk this loop. In **file
mode**, set it to `1` if you ticked a `fix_plan.md` checkbox. In
**linear mode**, comments and state transitions don't write to disk —
keep it `0`. `EXIT_SIGNAL` stays `false` — the harness will reinvoke
you for the next task.

### Read-only audit task

You picked up a ticket emitted by `tapps_audit_campaign` — it carries
the `audit-readonly` Linear label, or its body starts with
`<!-- ralph: audit-readonly -->`. The flow:

1. Run the tool sequence from `## Refs` of the ticket
   (`tapps_session_start` → per-file `tapps_quick_check` /
   `tapps_security_scan` / etc., then `tapps_impact_analysis` on
   sub-60 scores).
2. File P0/P1 findings as individual child Linear issues with
   `parent_id` set to this session ticket; bundle P2/P3 into one
   digest issue per session (also parented). Zero findings = post a
   `no findings, session clean` comment instead of filing.
3. **R1 is exempt** for audit work — no commit on `main` will exist
   because nothing was edited. Skip the `git log` check entirely.
4. Close the session ticket with a summary comment listing the filed
   findings (or "no findings") and move it to **Done**.

Status block:

```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: 1
FILES_MODIFIED: 0
TESTS_STATUS: NOT_RUN
WORK_TYPE: AUDIT
EXIT_SIGNAL: false
LINEAR_ISSUE: TAP-<session-id>
LINEAR_EPIC: TAP-<campaign-epic>
LINEAR_EPIC_DONE: <N>
LINEAR_EPIC_TOTAL: <total-sessions>
RECOMMENDATION: Continue with next audit session under TAP-<campaign-epic>
---END_RALPH_STATUS---
```

`FILES_MODIFIED` is `0` because audit work writes nothing to disk.
`TASKS_COMPLETED_THIS_LOOP: 1` is what tells the harness this loop
made progress — without it the no-progress counter would increment
even though the session ticket moved to Done. `EXIT_SIGNAL` stays
`false` until the campaign epic itself is complete (all session
children Done).

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

### Single task blocked on external dependency

The task you picked genuinely requires a credential, human decision, or
missing upstream that no reasonable workaround bypasses, **but other tasks
in the queue may still be actionable**. Pick a different unblocked task
instead — only emit this block if you've already attempted the queue and
this is the one you landed on:

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

### Whole queue blocked — clean exit

You assessed the entire open queue (every unchecked `- [ ]` in file mode,
every Backlog/Todo/In-Progress issue in linear mode) and **every single
item is blocked on external action you cannot resolve this loop**. There
is no actionable task to pick. This is the EXIT_SIGNAL gate's "Grounds 2"
case — emit `EXIT_SIGNAL: true` so the harness exits cleanly instead of
burning loops on a queue with nothing actionable in it:

```
---RALPH_STATUS---
STATUS: BLOCKED
TASKS_COMPLETED_THIS_LOOP: 0
FILES_MODIFIED: 0
TESTS_STATUS: NOT_RUN
WORK_TYPE: VERIFICATION
EXIT_SIGNAL: true
RECOMMENDATION: All N open issues blocked on [summary of blockers] — re-run after [unblock condition]
---END_RALPH_STATUS---
```

Use this **only** after a real queue-wide assessment, not as an escape
hatch for a single hard task. The harness will treat this as a clean exit
(no circuit-breaker increment) the same way it does `STATUS: COMPLETE` +
`EXIT_SIGNAL: true`.

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
- `.claude/agents/ralph*.md` and `.cursor/agents/ralph*.md` (agent definitions — edit via `ralph-upgrade` where applicable)
- `.claude/hooks/on-stop.sh` and `protect-ralph-files.sh` (edit via
  `ralph-upgrade`)
- `.claude/skills/ralph-workflow/` and `.cursor/skills/ralph-workflow/` (edit via `ralph-upgrade` or repo PRs)

If a cleanup/refactor task seems to require modifying any of these, stop
and re-read the task — almost always the task means code under `src/`, not
the harness itself.

## Python introspection — use snippet files, never `-c`

The Bash PreToolUse hook (`validate-command.sh`) blocks `python3 -c '…'`
(and the equivalent `-c` / `-e` flag in `python`, `node`, `ruby`, `perl`,
`bash`, `sh`, `zsh`) as a security gate against arbitrary in-loop code
execution. For ad-hoc introspection — parsing JSON tool output,
sanity-checking an import, measuring a string — write the snippet to
`/tmp/snippet.py` and run `python3 /tmp/snippet.py`. The hook allow-lists
`python3 <path>` because the path is auditable. Re-trying `python3 -c`
after a denial just burns another tool call: the block message itself
names the workaround, and the hook tokenizes argv so wrapping in `env`,
`bash -lc`, `uv run`, etc. does not bypass it. Full recipe (including
sibling interpreters) lives in the `python-introspection` skill.

## What not to do

- Don't run tests after every task (see epic-boundary rules).
- Don't skip the deslop pass at epic boundaries unless `RALPH_NO_DESLOP=true`.
- Don't continue with busywork after `EXIT_SIGNAL: true` would be correct.
- Don't refactor code that works. Don't add features outside the plan.
- Don't omit the status block. Without it the harness cannot tell what
  happened and counts the loop as no-progress.
- Don't emit the status block and then keep going. End of response = block.

## Revision history

This skill's `version` field is the contract pin downstream projects use
to know which behaviors the harness expects. Bump the minor when any of
the hard rules below change; bump the patch for clarifications.

- **1.2.0** — consolidates everything that landed against the original
  1.1.0 stamp without a version bump. No new contract relative to a
  project running the latest harness; this is a stamp catch-up so
  downstream projects can pin a known set of rules:
  - **R0** branch-first rule + harness-side block on `git push` to
    `main`/`master` (Ralph 2.15.6 / PR #27 + #29).
  - **R1 async-merge mode** under `RALPH_ASYNC_MERGE=true` —
    `gh pr create`, append to `.ralph/pending-merges.json`, stop the
    loop (Ralph 2.16.0 / PR #41).
  - **T3 sub-agent fan-out anti-patterns** — single-message multi-Task
    dispatch at epic boundary, FAIL/TIMEOUT collapse rule (PR #37).
  - **T4 brief lookahead** — `NEXT_INTENDED_ISSUE` field +
    `.ralph/brief-next.json` consumer (PR #39).
  - **`linear-read` skill mandatory** for any multi-issue Linear read
    (cache-first dance via `tapps_linear_snapshot_get`) (PR #36).
  - **F1+F2+F3 post-AgentForge feedback bundle** — periodic push,
    debrief stderr, exit-learning gate, Linear `state` field (PR #32).
  - **TAP-2256** — `python-introspection` skill referenced for ad-hoc
    Python (replaces `python3 -c` which `validate-command.sh` blocks).
  - **TAP-2349** — coordinator brief path discipline: write
    `.ralph/brief.json` (slash, under `.ralph/`), never
    `.ralph-brief.json` (dash, repo root).
- **1.1.0** — read-only audit-session support (R1 exemption when
  `WORK_TYPE: AUDIT` and `FILES_MODIFIED: 0`).
- **1.0.x** — initial per-loop execution contract.
