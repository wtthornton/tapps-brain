---
name: tapps-docs-bootstrap
user-invocable: true
description: >-
  Bootstrap documentation for a new or under-documented project: README,
  CONTRIBUTING, onboarding, completeness check. Use when creating a README,
  onboarding guide, or initial doc scaffold (Anthropic documentation skill parity).
allowed-tools: >-
  mcp__nlt-project-docs__docs_session_start
  mcp__nlt-project-docs__docs_module_map
  mcp__nlt-project-docs__docs_generate_readme
  mcp__nlt-project-docs__docs_generate_contributing
  mcp__nlt-project-docs__docs_generate_onboarding
  mcp__nlt-project-docs__docs_check_completeness
argument-hint: "[style: minimal|standard|comprehensive]"
---
<!-- BEGIN: tapps-skill tapps-docs-bootstrap v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Bootstrap project documentation end-to-end:

1. `mcp__nlt-project-docs__docs_session_start` — inventory gaps and recommendations.
2. `mcp__nlt-project-docs__docs_module_map` — understand structure (optional but recommended).
3. `mcp__nlt-project-docs__docs_generate_readme(style="standard", merge=true)` — create/update README.
4. `mcp__nlt-project-docs__docs_generate_contributing` — CONTRIBUTING.md.
5. `mcp__nlt-project-docs__docs_generate_onboarding` — docs/ONBOARDING.md.
6. `mcp__nlt-project-docs__docs_check_completeness` — target score ≥ 80 for bootstrap; list remaining gaps.

Hand-edit placeholders in onboarding/README before declaring done.
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

Bootstrap project documentation end-to-end:

1. `mcp__nlt-project-docs__docs_session_start` — inventory gaps and recommendations.
2. `mcp__nlt-project-docs__docs_module_map` — understand structure (optional but recommended).
3. `mcp__nlt-project-docs__docs_generate_readme(style="standard", merge=true)` — create/update README.
4. `mcp__nlt-project-docs__docs_generate_contributing` — CONTRIBUTING.md.
5. `mcp__nlt-project-docs__docs_generate_onboarding` — docs/ONBOARDING.md.
6. `mcp__nlt-project-docs__docs_check_completeness` — target score ≥ 80 for bootstrap; list remaining gaps.

Hand-edit placeholders in onboarding/README before declaring done.
