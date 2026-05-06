---
name: tapps-finish-task
user-invocable: true
model: claude-haiku-4-5-20251001
description: Run the end-of-task TAPPS pipeline in one shot — validate_changed, then checklist, then an optional memory save for anything architectural or patterned learned this session. The recommended final step before declaring work complete.
allowed-tools: mcp__tapps-mcp__tapps_validate_changed mcp__tapps-mcp__tapps_checklist mcp__tapps-mcp__tapps_memory
argument-hint: "[task_type: feature|bugfix|refactor|security|review]"
---

Close out the current task end-to-end. Run each step; do NOT skip one that failed — surface the failure and stop.

1. **Validate changed files.** Identify the files you edited this session (git status, your edit history). Call `mcp__tapps-mcp__tapps_validate_changed` with explicit `file_paths` (comma-separated) scoped to those files. **Never call without `file_paths`.** Default is quick mode. If any file fails, list it with the top blocking issue and stop — the task is not complete. Do not proceed to step 2 until all changed files pass.

2. **Verify the checklist.** Call `mcp__tapps-mcp__tapps_checklist(task_type=<feature|bugfix|refactor|security|review>)`. If the response has `complete: false`, the `missing_steps` list names required tools you skipped — address each (or explain why it does not apply) and re-run the checklist. Only proceed when `complete: true`.

3. **Save learnings (conditional).** If this session produced a non-obvious architectural or pattern-level decision — a new convention, a subtle trade-off, a gotcha someone else would re-discover — call `mcp__tapps-mcp__tapps_memory(action="save", tier=<"architectural"|"pattern">, ...)` with a concise body. Skip this step for routine fixes, refactors where the code itself documents the decision, or trivial bugfixes.

4. **Report.** Emit a one-line summary: `Files validated: N pass. Checklist: <task_type> complete. Memory saved: yes|no.` If any step failed or was skipped, say so explicitly.
