---
name: tapps-finish-task
description: >-
  Run the end-of-task TAPPS pipeline in one shot: validate changed files,
  verify the checklist, and optionally save learnings to memory. Use when
  you have finished implementing a task and want to validate, checklist,
  and save learnings in one shot.
mcp_tools:
  - tapps_validate_changed
  - tapps_checklist
---

Close out the current task end-to-end. Run each step; do NOT skip one that failed — surface the failure and stop.

1. **Validate changed files.** Identify files edited this session (git status, edit history). Call `tapps_validate_changed` with explicit `file_paths` (comma-separated). Never call without `file_paths`. If any file fails, list it with the top blocking issue and stop.
2. **Verify the checklist.** Call `tapps_checklist(task_type=<feature|bugfix|refactor|security|review>)`. If `complete: false`, address each entry in `missing_steps` and re-run.
3. **Save learnings (conditional).** If the session produced a non-obvious architectural or pattern-level decision, run `uv run tapps-mcp memory save --key <slug> --tier <architectural|pattern> --value "<decision>"` (no `tapps_memory` MCP). Skip for routine fixes. Brain offline → skip silently.
4. **Report.** Emit a one-line summary: `Files validated: N pass. Checklist: <task_type> complete. Memory saved: yes|no.`

5. **Transfer (optional).** If the user is ending the chat, invoke the `tapps-handoff-session` skill so the next session can run `tapps-continue-session`.
