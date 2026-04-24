# TappsMCP Quality Tools

This project uses TappsMCP for code quality analysis. When TappsMCP is
available as an MCP server (configured in `.vscode/mcp.json`), use the
following tools to maintain code quality throughout development.

## Key Tools

- `tapps_session_start` - Initialize a TappsMCP session at the start of
  each work session. Call this first.
- `tapps_quick_check` - Run a quick quality check on a single file after
  editing. Returns score and top issues.
- `tapps_quality_gate` - Run a pass/fail quality gate against a configurable
  preset (development, staging, or production).
- `tapps_validate_changed` - Validate all changed files against the quality
  gate. Call this before declaring work complete.
- `tapps_lookup_docs` - Look up library documentation and API references
  for external libraries and frameworks.
- `tapps_score_file` - Get a detailed 7-category quality score for any file.

## Workflow

1. Start a session: call `tapps_session_start`
2. After editing Python files: call `tapps_quick_check` on changed files
3. Before creating a PR or declaring work complete: call
   `tapps_validate_changed`
4. For library documentation: call `tapps_lookup_docs` with the
   library name and topic

## Quality Scoring Categories

TappsMCP scores code across 7 categories (0-100 each):
correctness, security, maintainability, performance, documentation,
testing, and style.

## Project Scope (do not break out of this repo/project)

This Copilot instance was configured for THIS repo by `tapps_init` /
`tapps_upgrade`. Reading docs across projects is fine; **writing** outside
this repo or the linked tracker project is not. Specifically:

- Do not create, update, comment on, or move issues that belong to a
  different project than this repo.
- Do not modify files, branches, or pull requests in any other repository.
- Read team / project identity from `.tapps-mcp.yaml` or the current git
  remote, not from arbitrary search results.
- If a task seems to require a write outside this repo/project, ask the
  user before proceeding.
