---
name: tapps-flow-frontend
user-invocable: true
model: claude-sonnet-5
description: >-
  Frontend work flow combining UX playbook and standard finish pipeline.
  Use when the task is primarily UI/UX implementation or accessibility.
allowed-tools: mcp__nlt-build__tapps_session_start mcp__nlt-build__tapps_domain_playbook mcp__nlt-build__tapps_lookup_docs mcp__nlt-build__tapps_quick_check mcp__nlt-build__tapps_validate_changed mcp__nlt-build__tapps_checklist
---
<!-- BEGIN: tapps-skill tapps-flow-frontend v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

1. Invoke `/tapps-domain-frontend` steps 1-5, **or** run this shortcut:
   - `tapps_domain_playbook(domain="user-experience")`
   - `tapps_lookup_docs` for UI libraries in scope
2. `/tapps-finish-task` with `task_type=frontend`
3. Optional persona: agency-agents Frontend Developer (voice only; TappsMCP owns gates)
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

1. Invoke `/tapps-domain-frontend` steps 1-5, **or** run this shortcut:
   - `tapps_domain_playbook(domain="user-experience")`
   - `tapps_lookup_docs` for UI libraries in scope
2. `/tapps-finish-task` with `task_type=frontend`
3. Optional persona: agency-agents Frontend Developer (voice only; TappsMCP owns gates)
