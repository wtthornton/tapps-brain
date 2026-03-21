---
name: ralph-loop
description: >
  Execute one Ralph development loop iteration. Reads fix_plan.md,
  implements the first unchecked task, verifies, and commits.
user-invocable: true
disable-model-invocation: false
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - Agent
argument-hint: "[task description override]"
---

## Current Status

!`bash -c 'RALPH_DIR=".ralph"; total=$(grep -c "^\- \[" "$RALPH_DIR/fix_plan.md" 2>/dev/null || echo 0); done=$(grep -c "^\- \[x\]" "$RALPH_DIR/fix_plan.md" 2>/dev/null || echo 0); echo "Tasks: $done/$total complete, $((total - done)) remaining"'`

## Execution Contract

1. Read `.ralph/fix_plan.md` — find the FIRST unchecked `- [ ]` item.
   If `$ARGUMENTS` is provided, use that as the task override instead.
2. Search the codebase for existing implementations (use ralph-explorer agent if available).
3. If the task uses an external library API, look up docs first.
4. Implement the smallest complete change.
5. Run targeted verification (lint/type/test for touched scope).
6. Update fix_plan.md: `- [ ]` to `- [x]`.
7. Commit with descriptive message.
8. Report status in RALPH_STATUS block.
9. **STOP immediately after the status block.**

## Constraints

- ONE task only. Stop after completing it.
- LIMIT testing to ~20% of effort.
- NEVER modify .ralph/ files except fix_plan.md checkboxes.
- Use ralph-explorer for codebase search, ralph-tester for verification (if available).

## Status Reporting

At the end of your response, include:

---RALPH_STATUS---
STATUS: IN_PROGRESS | COMPLETE | BLOCKED
TASKS_COMPLETED_THIS_LOOP: <number>
FILES_MODIFIED: <number>
TESTS_STATUS: PASSING | FAILING | NOT_RUN
WORK_TYPE: IMPLEMENTATION | TESTING | DOCUMENTATION | REFACTORING
EXIT_SIGNAL: false | true
RECOMMENDATION: <one line summary>
---END_RALPH_STATUS---
