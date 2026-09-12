---
name: tapps-flow-develop
user-invocable: true
model: claude-haiku-4-5-20251001
description: >-
  Standard feature/bugfix development flow via the shared TAPPS pipeline.
  Use when starting daily implementation work and you want session start,
  lookup docs, quick_check loop, and finish-task without a domain specialist.
allowed-tools: mcp__nlt-build__tapps_session_start mcp__nlt-build__tapps_lookup_docs mcp__nlt-build__tapps_quick_check mcp__nlt-build__tapps_validate_changed mcp__nlt-build__tapps_checklist Bash
argument-hint: "[task_type: feature|bugfix]"
---
<!-- BEGIN: tapps-skill tapps-flow-develop v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

1. `tapps_session_start()`
2. `tapps_lookup_docs` before each external library API
3. Edit loop: `tapps_quick_check` after Python edits
4. `/tapps-finish-task` with `task_type=feature` or `bugfix`
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

1. `tapps_session_start()`
2. `tapps_lookup_docs` before each external library API
3. Edit loop: `tapps_quick_check` after Python edits
4. `/tapps-finish-task` with `task_type=feature` or `bugfix`
