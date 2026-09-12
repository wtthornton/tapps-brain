---
name: tapps-docs-finish-task
description: >-
  Doc-work finish bundle: drift, links, cross-refs, completeness, optional
  release gate. Use when documentation edits are complete before merge/release.
mcp_tools:
  - docs_check_drift
  - docs_check_links
  - docs_check_cross_refs
  - docs_check_completeness
  - docs_release_gate
  - tapps_checklist
---
<!-- BEGIN: tapps-skill tapps-docs-finish-task v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Run drift → links → cross_refs → completeness; add `docs_release_gate` for releases; finish with `tapps_checklist(task_type=documentation)`.
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

Run drift → links → cross_refs → completeness; add `docs_release_gate` for releases; finish with `tapps_checklist(task_type=documentation)`.
