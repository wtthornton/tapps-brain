---
alwaysApply: true
---
# Linear Issue Standards (TappsMCP)

All Linear writes in this project — epic creation, story creation, issue updates — MUST route through the `linear-issue` skill, which in turn routes through the docs-mcp generator and validator tools. Raw calls to `mcp__plugin_linear_linear__save_issue` are a rule violation.

## Pre-filing reconciliation (TAP-2171)

**Before generating any new epic or story, run a 3-step check against the codebase to confirm the work is not already shipped.** The largest single source of wasted agent time on this project is filing tickets from epic-design documents whose acceptance criteria have already landed on `main` (three drift-pattern closures landed in one session on 2026-05-18 — see TAP-2131, TAP-2134, TAP-2135). The check costs < 2 minutes; the cost of skipping it is 15 minutes to 2 hours of audit / re-spec work.

The check is gated upstream of `docs_generate_epic` / `docs_generate_story` — when an AC is already shipped, you do not file the ticket; when **some** ACs are shipped, you carve out a narrower follow-up ticket (TAP-2170 is the worked example of the carve-out pattern).

### 1. Grep the commit history

For each major AC keyword (feature name, env-var name, module name, span name):

```bash
git log --grep="<keyword>" --all --oneline | head -20
git log --all --oneline -- src/<module>/ | head -20
```

Look for `feat(`, `fix(`, or `chore(` commits whose subject line names the AC. Spot-check the one or two most recent matches.

### 2. Grep the current code

For each AC that names a symbol, constant, env-var, or attribute:

```bash
grep -rn "<symbol>" src/ tests/ | head
```

A hit on `src/` means the symbol exists on `main`. Open the file and confirm the AC's *behaviour* matches what's there — names can collide. A hit on `tests/` is corroborating evidence the behaviour is exercised.

### 3. Diff the ticket draft against the shipped behaviour

Read each AC line and mark it:

- **(a) verified-open** — symbol does not exist, behaviour is not observable on `main`. Keep the AC.
- **(b) already-shipped** — symbol exists and behaviour matches. Drop the AC; record the shipped commit hash under "Already-shipped (closed-out)".
- **(c) partially-shipped** — some sub-bullets shipped, others not. Carve the unshipped slice into a narrower ticket (TAP-2170 pattern); record the shipped commits on the original draft and link the new narrow ticket.

If **every** AC is (b), do not file the ticket. If the parent design doc (an `EPIC-*.md` in `docs/planning/epics/`) still describes the work as open, update that file in the same PR that closes the would-be ticket.

### Worked example — TAP-2135 (OTel GenAI semconv)

Original draft AC list (paraphrased):

1. Emit `gen_ai.system` on MCP tool-call spans
2. Emit `gen_ai.tool.name` on MCP tool-call spans
3. Emit `gen_ai.operation.name = "execute_tool"`
4. Emit `mcp.method.name`
5. Emit `gen_ai.data_source.id`
6. Add structured retrieval events to recall spans
7. Capture content opt-in gated by `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`

Step 1 — `git log --grep="STORY-032" --oneline | head -20` shows ten commits from STORY-032.2 through STORY-032.10.

Step 2 — `grep -rn 'gen_ai\.system\|GEN_AI_SYSTEM' src/` shows the constant defined in `src/tapps_brain/otel_tracer.py:93` and set on every span in `start_mcp_tool_span`. Same pattern for `gen_ai.tool.name`, `gen_ai.operation.name`, `mcp.method.name`, retrieval events, and content-capture opt-in. `grep -rn 'gen_ai\.data_source\.id' src/` returns nothing.

Step 3 — ACs 1–4, 6, 7 are (b) already-shipped (refs: STORY-032.2 through STORY-032.10). AC 5 is (a) verified-open.

**Outcome:** Close the original ticket as Done with commit annotations; carve AC 5 into a one-point follow-up ticket (TAP-2170) with the file anchors found in step 2. Total time: ~10 minutes. Filing the original ticket as-is would have wasted at least an hour of re-discovery work.

## Required flow

### For a new epic
1. `mcp__docs-mcp__docs_generate_epic(title, purpose_and_intent, goal, motivation, acceptance_criteria, stories, ...)` — produces `docs/epics/EPIC-<N>.md` in the template shape.
2. `mcp__docs-mcp__docs_validate_linear_issue(title, description, is_epic=true)` — must return `agent_ready: true` with score 100.
3. `mcp__plugin_linear_linear__save_issue(..., assignee="<agent-user-id-or-name>")` to push. Default assignee = the agent identity, never the OAuth human (see `autonomy.md`). Do NOT pause to confirm with the user — the original request is the authorization.
4. Create each child story via the story flow with `parent_id=<epic TAP-id>` (each child also assigned to the agent).
5. `mcp__tapps-mcp__tapps_linear_snapshot_invalidate(team, project)`.

### For a new story
1. `mcp__docs-mcp__docs_generate_story(title, files, acceptance_criteria, ...)` — emits the 5-section template (`## What` / `## Where` / `## Why` / `## Acceptance` / `## Refs`).
2. `mcp__docs-mcp__docs_validate_linear_issue(title, description)` — must return `agent_ready: true`.
3. `mcp__plugin_linear_linear__save_issue(..., parent_id=<epic>, assignee="<agent-user-id-or-name>")`. Default assignee = the agent identity (see `autonomy.md`); proceed without a confirmation prompt.
4. `mcp__tapps-mcp__tapps_linear_snapshot_invalidate(team, project)`.

### Before updating an existing issue
1. `mcp__plugin_linear_linear__get_issue(id)` — fetch current state.
2. `mcp__docs-mcp__docs_lint_linear_issue(title, description, labels, priority, estimate)` — surface findings.
3. Regenerate via `docs_generate_story` or manual edit only if the existing body is broken.
4. Validate before push.
5. `save_issue(id=..., description=...)`; invalidate cache.

### For multi-issue reads (TAP-1260)
All list-style Linear reads route through the `linear-read` skill (4-step cache-first dance: `snapshot_get` → on miss `list_issues` → `snapshot_put` → use cached on hit). Single-issue lookups go straight to `get_issue(id)` — never via filtered `list_issues`. Raw `mcp__plugin_linear_linear__list_issues` calls without a prior `snapshot_get` for the same key are a rule violation. See the `linear-read` skill for the antipattern catalogue (6-poll kickoff, status-bucket sweep, unfiltered scroll).

## Assignee defaults

All Linear writes from this project — epics, stories, subtasks, triage updates — default to the **agent** as assignee, never a human (see `autonomy.md`):

1. Resolve once per session: `mcp__plugin_linear_linear__list_users` → pick the user whose `name` / `displayName` / `email` matches `agent`, `bot`, `tapps`, `claude`, or `agent_user` in `.tapps-mcp.yaml`. Cache the id.
2. Pass `assignee="<agent-user-id-or-name>"` to every `save_issue` call.
3. If no agent user exists, leave `assignee` unset. **Do NOT fall back to the OAuth user.**
4. Override only when the user explicitly names a different assignee in the request.

## Formatting rules (enforced by docs-mcp validator)

- Title <= 80 characters; no em-dash preambles.
- `## Acceptance` must contain at least one `- [ ]` checkbox.
- `## Where` must contain at least one `file.ext:LINE-RANGE` anchor.
- Bare `TAP-###` references, never `<issue id="UUID">TAP-###</issue>` wrappers.

## Linear markdown workarounds (observed 2026-04-24)

Linear's server-side markdown processor silently drops some content. These patterns preserve data:

- **Numbered lists, not bulleted, in `## Where` and `## Acceptance`** when items reference file paths. Bulleted `* path/...` entries get deduped on auto-linked filenames (especially `.md` files), keeping only the first. Numbered lists (`1.`, `2.`) survive intact.
- **Inline-code file paths**: `` `path/to/file.py:1-100` `` rather than bare `path/to/file.py:1-100`. Prevents the auto-linker from mangling.
- **Don't write bare `.md` filenames in prose** when a markdown auto-link would interfere. Use "the agents-md template", "the claude-md file", or wrap in backticks.
- **Avoid tables with many columns** — Linear's table rendering is fragile; prefer numbered lists with `—` separators for row fields.

## How to apply

When the user says "create a Linear issue", "file an epic", "open a ticket for X", or "track this in Linear" — invoke the `linear-issue` skill. Do not call `save_issue` directly. If the skill is unavailable in the session, flag it to the user rather than falling back to raw writes.

When the user says "list Linear issues", "what's open in TAP", "find issues assigned to X", or "review the backlog" — invoke the `linear-read` skill. Do not call `list_issues` directly. Single-issue lookups (user has an id like "TAP-686") go straight to `get_issue` without the skill.

When updating an existing issue, the same routing applies: fetch, lint/validate, regenerate or edit, re-validate, save, invalidate.

## Release Updates

When the user says "post a release update", "announce vX.Y.Z", "ship release-update", or "log this release to Linear" — invoke the `linear-release-update` skill. Do not call `save_document` directly.

**Flow:** `tapps_release_update(version, prev_version)` → check `agent_ready=true` →
`save_document(project=data.project, title=data.document_title, content=data.body)` →
`tapps_linear_snapshot_invalidate(team=data.team, project=data.project)`.

Never call `save_document` without a prior `agent_ready=true` from `tapps_release_update`.
The `document_title` field from the tool response must be used verbatim (em-dash format).
With `dry_run=True`, the tool returns the body without gating on `agent_ready`.

## Enforcement

### Writes (TAP-981)

Hard-enforced via hooks in `.claude/settings.json`:

- **PostToolUse** on `mcp__docs-mcp__docs_validate_linear_issue` → `.claude/hooks/tapps-post-docs-validate.sh` writes a sentinel to `.tapps-mcp/.linear-validate-sentinel`.
- **PreToolUse** on `mcp__plugin_linear_linear__save_issue` → `.claude/hooks/tapps-pre-linear-write.sh` blocks the call if the sentinel is missing or > 30 minutes old. Bypass with `TAPPS_LINEAR_SKIP_VALIDATE=1` (logged to `.tapps-mcp/.bypass-log.jsonl`).

### Reads (TAP-1224)

Hard-enforced via the cache-first read gate. Mode controlled by `linear_enforce_cache_gate` in `.tapps-mcp.yaml` (`off` | `warn` | `block`; default `warn` at high/medium engagement, `off` at low):

- **PostToolUse** on `mcp__tapps-mcp__tapps_linear_snapshot_get` → `.claude/hooks/tapps-post-linear-snapshot-get.sh` writes a per-`(team, project, state, label, limit)` sentinel at `.tapps-mcp/.linear-snapshot-sentinel-<key>` on **both** `cached=true` and `cached=false` responses. When `state` is `open` (a tapps-mcp TTL bucket alias), `''`, or any open-bucket member (`backlog`/`unstarted`/`started`/`triage`), the hook ALSO writes alias sentinels for every other open-bucket state (TAP-1374) so concrete `list_issues` calls don't self-trip the gate.
- **PostToolUse** on `mcp__plugin_linear_linear__list_issues` → `.claude/hooks/tapps-post-linear-list.sh` auto-populates `.tapps-mcp-cache/linear-snapshots/<key>.json` directly from the response payload (TAP-1412). Eliminates the agent's manual `tapps_linear_snapshot_put` step that was being skipped, leaving the cache empty.
- **PreToolUse** on `mcp__plugin_linear_linear__list_issues` → `.claude/hooks/tapps-pre-linear-list.sh` derives the same sentinel key from the call args and:
  - **warn mode** (default): logs the violation to `.tapps-mcp/.cache-gate-violations.jsonl` and lets the call through. Use the first release for telemetry; `tapps doctor` reports the 24-hour violation count.
  - **block mode**: rejects the call with exit 2 unless a matching sentinel < 300 s old exists. Bypass with `TAPPS_LINEAR_SKIP_CACHE_GATE=1` (logged to `.tapps-mcp/.bypass-log.jsonl`).
  - **cross-project tagging (TAP-1411)**: when the call's `team`/`project` differ from this repo's `linear_team`/`linear_project` in `.tapps-mcp.yaml`, the violation is tagged `category: cross_project` and passes through regardless of mode (agent-scope.md allows cross-project READS). Same-project misses are tagged `category: gate_miss`.
- **No exempt parameters.** Single-issue lookups must use `mcp__plugin_linear_linear__get_issue(id=...)`. There is no `query=` / `parentId=` / `cycle=` exemption — every multi-issue read goes through `tapps_linear_snapshot_get` first (the `linear-read` skill from TAP-1260 routes this for you).
- **Per-key isolation.** A snapshot_get for project A does **not** unlock a list_issues for project B — the sentinel hash includes team, project, state, label, and limit so cross-slice unlock is impossible.
- **Violation-log schema.** Each line in `.cache-gate-violations.jsonl` carries `ts`, `key`, `mode`, `category` (`gate_miss`|`cross_project`), `call_team`, `call_project`. Older lines (pre-TAP-1411) lack the category field — treat as `gate_miss`.
