---
name: tapps-docs-generate
description: >-
  Quick doc generation: README, llms.txt, changelog. Use for a minimal
  generate pass; prefer tapps-docs-bootstrap for new projects.
allowed-tools: >-
  mcp__nlt-project-docs__docs_generate_readme
  mcp__nlt-project-docs__docs_generate_llms_txt
  mcp__nlt-project-docs__docs_generate_changelog
  mcp__nlt-project-docs__docs_generate_runbook
  mcp__nlt-project-docs__docs_generate_postmortem
---
<!-- BEGIN: tapps-skill tapps-docs-generate v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Generate documentation artifacts:

1. `mcp__nlt-project-docs__docs_generate_readme(merge=true)`
2. `mcp__nlt-project-docs__docs_generate_llms_txt(mode="compact")`
3. `mcp__nlt-project-docs__docs_generate_changelog` when git tags exist
4. For operational docs: `docs_generate_runbook` / `docs_generate_postmortem` with structured fields
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

Generate documentation artifacts:

1. `mcp__nlt-project-docs__docs_generate_readme(merge=true)`
2. `mcp__nlt-project-docs__docs_generate_llms_txt(mode="compact")`
3. `mcp__nlt-project-docs__docs_generate_changelog` when git tags exist
4. For operational docs: `docs_generate_runbook` / `docs_generate_postmortem` with structured fields
