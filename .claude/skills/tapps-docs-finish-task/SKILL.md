---
name: tapps-docs-finish-task
user-invocable: true
description: >-
  End-of-doc-work validation bundle: drift, links, cross-refs, completeness,
  optional release gate. Use when documentation edits are complete and you
  need a pass/fail verdict before merging or releasing.
allowed-tools: >-
  mcp__nlt-project-docs__docs_check_drift
  mcp__nlt-project-docs__docs_check_links
  mcp__nlt-project-docs__docs_check_cross_refs
  mcp__nlt-project-docs__docs_check_completeness
  mcp__nlt-project-docs__docs_release_gate
  mcp__nlt-build__tapps_checklist
argument-hint: "[--release]"
---
<!-- BEGIN: tapps-skill tapps-docs-finish-task v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Close out documentation work:

1. `mcp__nlt-project-docs__docs_check_drift` — stop if critical undocumented APIs (report count).
2. `mcp__nlt-project-docs__docs_check_links(broken_only=true)` — stop on broken internal links.
3. `mcp__nlt-project-docs__docs_check_cross_refs(doc_dirs="docs")` — orphans and broken refs.
4. `mcp__nlt-project-docs__docs_check_completeness` — target ≥ 90 for merge-ready.
5. **Release only:** `mcp__nlt-project-docs__docs_release_gate` — aggregate verdict; stop if fail.
6. `mcp__nlt-build__tapps_checklist(task_type=documentation)` — TAPPS doc-workflow checklist.

**Report:** `Drift: N findings. Links: pass|fail. Completeness: X/100. Release gate: pass|skipped|fail.`
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

Close out documentation work:

1. `mcp__nlt-project-docs__docs_check_drift` — stop if critical undocumented APIs (report count).
2. `mcp__nlt-project-docs__docs_check_links(broken_only=true)` — stop on broken internal links.
3. `mcp__nlt-project-docs__docs_check_cross_refs(doc_dirs="docs")` — orphans and broken refs.
4. `mcp__nlt-project-docs__docs_check_completeness` — target ≥ 90 for merge-ready.
5. **Release only:** `mcp__nlt-project-docs__docs_release_gate` — aggregate verdict; stop if fail.
6. `mcp__nlt-build__tapps_checklist(task_type=documentation)` — TAPPS doc-workflow checklist.

**Report:** `Drift: N findings. Links: pass|fail. Completeness: X/100. Release gate: pass|skipped|fail.`
