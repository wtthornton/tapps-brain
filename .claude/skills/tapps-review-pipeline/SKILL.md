---
name: tapps-review-pipeline
user-invocable: true
model: claude-sonnet-5
description: >-
  Orchestrate a parallel review-fix-validate pipeline across multiple changed files.
  Spawns tapps-review-fixer agents in worktrees for parallel processing. Use when
  you have multiple changed Python files that need parallel review, scoring, and
  quality gate fixing before declaring work complete.
allowed-tools: mcp__nlt-build__tapps_validate_changed mcp__nlt-build__tapps_checklist
context: fork
agent: general-purpose
---
<!-- BEGIN: tapps-skill tapps-review-pipeline v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Run a parallel review-fix-validate pipeline on changed Python files:

1. Call `mcp__nlt-build__tapps_session_start` if not already called
2. Determine scope: detect changed Python files via git diff or accept a file list
3. For each file (or batch of files), spawn a `tapps-review-fixer` agent in a worktree:
   - Use the Task tool with `subagent_type: "general-purpose"` and `isolation: "worktree"`
   - Pass the file path and instructions to score, fix, and gate the file
4. Wait for all agents to complete and collect their results
5. Merge any worktree changes back (review diffs before accepting)
6. Call `mcp__nlt-build__tapps_validate_changed` with explicit `file_paths` to verify all files pass
7. **Creator ≠ verifier:** the review-fixer agents that *implemented* fixes must not be the sole
   judges. Spawn a fresh review pass (or Bugbot / tapps-reviewer) that did not write the fixes,
   then `uv run tapps-mcp pipeline-mark creator-verifier`.
8. Call `mcp__nlt-build__tapps_checklist(task_type="review")` for final verification — clear
   `creator_verifier_skipped` / `contract_assertions_unverified` if present
9. Present a summary table: file | before score | after score | gate | fixes applied
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

Run a parallel review-fix-validate pipeline on changed Python files:

1. Call `mcp__nlt-build__tapps_session_start` if not already called
2. Determine scope: detect changed Python files via git diff or accept a file list
3. For each file (or batch of files), spawn a `tapps-review-fixer` agent in a worktree:
   - Use the Task tool with `subagent_type: "general-purpose"` and `isolation: "worktree"`
   - Pass the file path and instructions to score, fix, and gate the file
4. Wait for all agents to complete and collect their results
5. Merge any worktree changes back (review diffs before accepting)
6. Call `mcp__nlt-build__tapps_validate_changed` with explicit `file_paths` to verify all files pass
7. **Creator ≠ verifier:** the review-fixer agents that *implemented* fixes must not be the sole
   judges. Spawn a fresh review pass (or Bugbot / tapps-reviewer) that did not write the fixes,
   then `uv run tapps-mcp pipeline-mark creator-verifier`.
8. Call `mcp__nlt-build__tapps_checklist(task_type="review")` for final verification — clear
   `creator_verifier_skipped` / `contract_assertions_unverified` if present
9. Present a summary table: file | before score | after score | gate | fixes applied
