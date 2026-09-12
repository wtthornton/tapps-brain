---
name: tapps-domain-testing
description: >-
  Testing-focused TAPPS workflow: playbook, pytest docs, diff impact, and validation. Use when adding tests, fixing test gaps, or validating affected tests after refactors.
mcp_tools:
  - tapps_session_start
  - tapps_domain_playbook
  - tapps_lookup_docs
  - tapps_quick_check
  - tapps_validate_changed
  - tapps_checklist
  - tapps_diff_impact
  - tapps_call_graph
---
<!-- BEGIN: tapps-skill tapps-domain-testing v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Domain playbook workflow — same quality gate as the standard TAPPS pipeline.

1. **Session bootstrap.** Call `session_start()` if not already called this session.
2. **Load playbook.** Call `domain_playbook(domain="testing-strategies")` (or read bundled checklist from the response). Follow its workflow and checklist.
3. **Library docs.** For each entry in `lookup_hints`, call `lookup_docs(library=..., topic=...)` before using those APIs.
4. **Domain tools.** Run the tools listed in `recommended_tools` on changed files in scope.
5. **Edit loop.** After each Python file change, call `quick_check(file_path=...)`.
4b. Call `diff_impact(file_paths=...)` to rank affected tests.
6. **Close out.** Invoke `/tapps-finish-task` with the task_type=qa. Do not declare done without validate + checklist.
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

Domain playbook workflow — same quality gate as the standard TAPPS pipeline.

1. **Session bootstrap.** Call `session_start()` if not already called this session.
2. **Load playbook.** Call `domain_playbook(domain="testing-strategies")` (or read bundled checklist from the response). Follow its workflow and checklist.
3. **Library docs.** For each entry in `lookup_hints`, call `lookup_docs(library=..., topic=...)` before using those APIs.
4. **Domain tools.** Run the tools listed in `recommended_tools` on changed files in scope.
5. **Edit loop.** After each Python file change, call `quick_check(file_path=...)`.
4b. Call `diff_impact(file_paths=...)` to rank affected tests.
6. **Close out.** Invoke `/tapps-finish-task` with the task_type=qa. Do not declare done without validate + checklist.
