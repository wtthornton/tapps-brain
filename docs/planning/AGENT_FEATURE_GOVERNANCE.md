# Agent Feature Governance

Last updated: 2026-03-27
Audience: all coding agents and human operators creating or triaging `feat` work

## Purpose

This file defines the required operating rules for agent-created feature requests so we minimize noise and keep delivery aligned to value.

## Required Inputs Before Any `feat` Issue

Agents must read and apply:

- `docs/planning/FEATURE_FEASIBILITY_CRITERIA.md`
- `docs/planning/open-issues-roadmap.md`
- `docs/planning/ISSUE_TRIAGE_VIEWS.md`

If any of the above are missing from the workflow, do not open a `feat` issue.

## Mandatory Workflow

1. Confirm the problem is real and frequent (not hypothetical).
2. Check for overlap in current CLI/API/MCP/docs/code.
3. Complete the full criteria scorecard and hard-gate checklist.
4. Decide one outcome: `approve`, `approve_with_rescope`, `defer`, `reject`.
5. If approved, assign the matching triage label:
   - `triage:approved`
   - `triage:rescope`
   - `triage:defer`
   - `triage:close-candidate`
6. Add explicit next action to the issue comment.

## GitHub Operating Conventions

- **Issue state** remains GitHub-native: `Open` or `Closed`.
- **Execution status** should be tracked in GitHub Projects Status field (recommended):
  - `Backlog`, `Ready`, `In progress`, `In review`, `Blocked`, `Done`
- **Milestones** should be used for timeboxed delivery windows (release/sprint).
- **Labels** classify work; they do not replace project execution state.

## Hard Stops (Do Not Open As `feat`)

- Duplicate capability already shipped
- No measurable success metric
- Violates deterministic/synchronous/storage architecture invariants
- High operational burden without clear ROI
- No rollback or compatibility strategy

Convert these to `docs`, `housekeeping`, or `spike` instead.

## Enforcement

- Feature issues created without completed criteria are considered invalid until remediated.
- Agents should add a triage comment with decision and rationale before implementation starts.
- For `close-candidate`, verify against acceptance criteria and close with evidence if complete.
