---
name: linear-issue
user-invocable: true
model: claude-haiku-4-5-20251001
description: Create, lint, validate, or triage Linear issues for agents. Routes to docs-mcp Linear tools and the Linear plugin by user intent.
allowed-tools: mcp__docs-mcp__docs_generate_story mcp__docs-mcp__docs_lint_linear_issue mcp__docs-mcp__docs_validate_linear_issue mcp__docs-mcp__docs_linear_triage mcp__plugin_linear_linear__get_issue mcp__plugin_linear_linear__list_issues mcp__tapps-mcp__tapps_linear_snapshot_get mcp__tapps-mcp__tapps_linear_snapshot_put mcp__tapps-mcp__tapps_linear_snapshot_invalidate
argument-hint: "[create|lint TAP-###|validate|triage] [free-form detail]"
---

Work with Linear issues for AI-agent consumption. Infer intent from the user's prompt; never write to Linear without explicit confirmation.

**Create** a new issue (default when prompt describes a change/bug):
1. Call `mcp__docs-mcp__docs_generate_story` with the user's ask. Required args: `title` (<=80 chars, pattern `file.py: symptom`), `files` (comma-separated, each with `:LINE-RANGE`), `acceptance_criteria` (verifiable items).
2. Default `audience="agent"` emits the 5-section Linear template (What/Where/Why/Acceptance/Refs) and round-trips through the validator.
3. If the call returns `INPUT_INVALID`, refine the inputs per the error message and retry. Do NOT pass `audience="human"` unless the user asks for a product-review doc.
4. Print the emitted markdown. Ask the user whether to create in Linear; only then call the Linear plugin's `save_issue`.
5. After `save_issue` returns, call `mcp__tapps-mcp__tapps_linear_snapshot_invalidate(team=<team>, project=<project>)` to evict stale cached snapshots for that slice.

**Lint** an existing issue (prompt like "lint TAP-686", "check TAP-###"):
1. Fetch via `mcp__plugin_linear_linear__get_issue`.
2. Pass title/description/labels/priority/estimate to `mcp__docs-mcp__docs_lint_linear_issue`.
3. Surface score, findings (with fix_hints), and reclaimable noise bytes. For each HIGH severity finding, quote the suggested fix.

**Validate** before creating or after editing (prompt like "is this agent-ready?"):
1. Call `mcp__docs-mcp__docs_validate_linear_issue` with the payload.
2. Report `{agent_ready, score, missing[]}`. Missing items are blockers; propose a concrete fix per item.

**Triage** a batch (prompt like "triage open issues", "find label gaps"):
1. If the user names a specific issue (e.g. "triage TAP-686"), use `mcp__plugin_linear_linear__get_issue(id="TAP-686")` — skip list/cache entirely.
2. **Cache-first read:** call `mcp__tapps-mcp__tapps_linear_snapshot_get(team=<team>, project=<project>, state="backlog" | "unstarted", label?)`. If `data.cached` is `true`, use `data.issues` directly — Linear was not called.
3. **On cache miss** (`data.cached` is `false`): call `mcp__plugin_linear_linear__list_issues` with narrow filters — `team`, `project`, `state`, `includeArchived=false` (never call without filters). Then populate the cache by calling `mcp__tapps-mcp__tapps_linear_snapshot_put(team, project, issues_json=json.dumps(response.issues), state, label?)` using the **same** team/project/state/label/limit as the get call so the keys align.
4. Pass the list to `mcp__docs-mcp__docs_linear_triage`.
5. Present label_proposals, parent_groupings, and metadata_gaps. Confirm with user before applying any changes via Linear plugin writes.
6. After any write, call `mcp__tapps-mcp__tapps_linear_snapshot_invalidate(team=<team>, project=<project>)` to refresh the cache on next read.

Rules (enforced by docs-mcp tools):
- Title <=80 chars; no em-dash preambles.
- Inline-code filenames (`AGENTS.md`), never `[AGENTS.md](AGENTS.md)` (Linear's autolinker mangles).
- Bare `TAP-###` refs, never `<issue id="UUID">TAP-###</issue>` wrappers.
- `## Acceptance` has at least one verifiable `- [ ]` item.
- `## Where` includes at least one `path/to/file.ext:LINE-RANGE` anchor.
