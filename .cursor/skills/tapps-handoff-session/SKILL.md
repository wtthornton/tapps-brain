---
name: tapps-handoff-session
description: >-
  Write a structured cross-session handoff and close the TAPPS session
  lifecycle so the next chat can continue without a long paste. Use when
  ending a session, handing off to a fresh chat, or the user says hand
  off, save session state, or continue next time.
mcp_tools:
  - tapps_handoff_save
  - tapps_session_start
---
<!-- BEGIN: tapps-skill tapps-handoff-session v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

End the session with a durable handoff the next chat loads via `tapps-continue-session`.

0. **Session bootstrap (if needed).** If `tapps_session_start()` was not called this session, call it now (cached is fine) so flywheel scope and checker context are correct. Skip when already called.

1. **Draft handoff (5-10 bullets):** Done, Open, Next (P0), Blockers (`- none` when clear), optional Changed files, Verify, Success criterion.**Checkpoint trigger:** when the user says "checkpoint", "context full", or an
   orchestration prompt prints a `CHECKPOINT` block — include the **Cumulative**
   section above (not optional). Cross-ref: orchestration-prompt method §7.

**P0 gate.** Before persisting: when **Open** has real items (not `none` / `- ...` placeholders), **Next (P0)** must name one concrete next action. Set **Linear P0:** to the TAP id when known. If P0 is missing, ask the user once — do not persist an incomplete handoff.

```markdown
# Session handoff
**Program:** <program or campaign name>
**Updated:** <ISO-8601 UTC from date -u>
**Git:** <short-sha or omit>
**Linear P0:** <TAP-#### or none>

## Done
- ...

## Open
- ...

## Next (P0)
- ... (plain prose; put TAP-#### in **Linear P0** above)

## Blockers
- none

## Changed files
- ... (optional; top paths from git status when multi-file)

## Verify
- ...

## Success criterion
- ...

## Cumulative (loop checkpoints — required for shift boundaries)
- Sub-goal: <k> · VAL IDs: <…>
- Attempt: <a> of <cap> (cumulative across shifts)
- Budget spent: <spent>/<ceiling>
- Refuted strategies: <bullets>
- Resume line: <exact cold-start launch line from prompt>
```

2. **Persist (one atomic call when MCP is available).** Do **not** write the file separately before MCP — `tapps_handoff_save` writes `.tapps-mcp/session-handoff.md`, lints, mirrors to brain, and can close the session lifecycle.

   Draft the full markdown in memory using the shape above:
   - **Program:** the program or campaign this session belongs to. It is the ownership key: the guard compares it against whoever wrote the file last, and only a *different* stated program is a conflict. Leave the placeholder in and the write is reported as unknown ownership — archived, never refused, but nobody can tell your handoff from anyone else's.
   - **Updated:** run `date -u +%Y-%m-%dT%H:%M:%SZ` — never a placeholder like `T00:00:00Z`
   - **Git:** `git rev-parse --short HEAD` when inside a git repo
   - **Linear P0:** TAP-#### when known (preferred retrieval key for brain session search)
   - **Blockers:** `- none` alone when clear — put user actions under **Verify** or **Next (P0)**, not Blockers
   - **Changed files:** optional bullets from `git status --short` when the session touched many files

   | Priority | When | How |
   |----------|------|-----|
   | 1 (MCP) | `nlt-memory` available | `tapps_handoff_save(markdown=..., session_end=true)` — single call; do **not** also call `tapps_session_end` |
   | 2 (CLI atomic) | Shell auth; no MCP write | `uv run tapps-mcp handoff write --file <draft.md> [--slot <your-program>] --session-end` — `--file` is the **input** to read, `--slot` picks the **destination** |
   | 3 (manual) | Brain HTTP only | `uv run tapps-mcp memory save --key session-handoff --tier context --tags handoff,cross-session --value "$(cat .tapps-mcp/session-handoff.md)"` — full markdown body |
   | 4 (skip) | Brain offline | File-only via Bash heredoc: `mkdir -p .tapps-mcp && cat > .tapps-mcp/session-handoff.md <<'EOF'` … `EOF` |

   **Slots — when another program shares this repo.** `slot="<your-program>"` writes `.tapps-mcp/handoffs/<slot>.md` and brain key `session-handoff.<slot>` instead of the shared default, so concurrent programs stop overwriting each other. Lowercase letters, digits and dashes, at most 48 characters. Omit it and you write the shared file, which is correct for a repo running one program at a time.

   **When the response carries `conflict`.** Print it. `foreign: true` means you replaced another program's handoff — name the program from `conflict.previous` and the recovery path from `conflict.archived_to`; the right fix is almost always to re-save under your own `slot=`. `foreign: "unknown"` means nobody could tell (no **Program:** header on one side) — say so rather than reporting a clean write. Under `handoff_conflict_mode: block` the save is **refused** with `handoff_owner_conflict`: retry with `slot=`, or pass `force=true` only when you genuinely mean to take over the shared file (the incumbent is archived first either way).

   Handoff **Updated** older than 7 days: pass `allow_lint_warnings=true` on `tapps_handoff_save` if lint warns on age.

3. **Report.** `Handoff: .tapps-mcp/session-handoff.md. Linear P0: <id|none>. brain_mirror: ok|skipped. session_end: ok|skipped. Next: tapps-continue-session`
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 7 heading(s) duplicate the managed block above verbatim (## Done, ## Open, ## Next (P0), ## Blockers, ## Changed files, ## Verify, ## Success criterion); 90% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

End the session with a durable handoff the next chat loads via `tapps-continue-session`.

0. **Session bootstrap (if needed).** If `tapps_session_start()` was not called this session, call it now (cached is fine) so flywheel scope and checker context are correct. Skip when already called.

1. **Draft handoff (5-10 bullets):** Done, Open, Next (P0), Blockers (`- none` when clear), optional Changed files, Verify, Success criterion.**Checkpoint trigger:** when the user says "checkpoint", "context full", or an
   orchestration prompt prints a `CHECKPOINT` block — include the **Cumulative**
   section above (not optional). Cross-ref: orchestration-prompt method §7.

**P0 gate.** Before persisting: when **Open** has real items (not `none` / `- ...` placeholders), **Next (P0)** must name one concrete next action. Set **Linear P0:** to the TAP id when known. If P0 is missing, ask the user once — do not persist an incomplete handoff.

```markdown
# Session handoff
**Updated:** <ISO-8601 UTC from date -u>
**Git:** <short-sha or omit>
**Linear P0:** <TAP-#### or none>

## Done
- ...

## Open
- ...

## Next (P0)
- ... (plain prose; put TAP-#### in **Linear P0** above)

## Blockers
- none

## Changed files
- ... (optional; top paths from git status when multi-file)

## Verify
- ...

## Success criterion
- ...

## Cumulative (loop checkpoints — required for shift boundaries)
- Sub-goal: <k> · VAL IDs: <…>
- Attempt: <a> of <cap> (cumulative across shifts)
- Budget spent: <spent>/<ceiling>
- Refuted strategies: <bullets>
- Resume line: <exact cold-start launch line from prompt>
```

2. **Persist (one atomic call when MCP is available).** Do **not** write the file separately before MCP — `tapps_handoff_save` writes `.tapps-mcp/session-handoff.md`, lints, mirrors to brain, and can close the session lifecycle.

   Draft the full markdown in memory using the shape above:
   - **Updated:** run `date -u +%Y-%m-%dT%H:%M:%SZ` — never a placeholder like `T00:00:00Z`
   - **Git:** `git rev-parse --short HEAD` when inside a git repo
   - **Linear P0:** TAP-#### when known (preferred retrieval key for brain session search)
   - **Blockers:** `- none` alone when clear — put user actions under **Verify** or **Next (P0)**, not Blockers
   - **Changed files:** optional bullets from `git status --short` when the session touched many files

   | Priority | When | How |
   |----------|------|-----|
   | 1 (MCP) | `nlt-memory` available | `tapps_handoff_save(markdown=..., session_end=true)` — single call; do **not** also call `tapps_session_end` |
   | 2 (CLI atomic) | Shell auth; no MCP write | `uv run tapps-mcp handoff write --file .tapps-mcp/session-handoff.md --session-end` after writing the file locally |
   | 3 (manual) | Brain HTTP only | `uv run tapps-mcp memory save --key session-handoff --tier context --tags handoff,cross-session --value "$(cat .tapps-mcp/session-handoff.md)"` — full markdown body |
   | 4 (skip) | Brain offline | File-only via Bash heredoc: `mkdir -p .tapps-mcp && cat > .tapps-mcp/session-handoff.md <<'EOF'` … `EOF` |

   Handoff **Updated** older than 7 days: pass `allow_lint_warnings=true` on `tapps_handoff_save` if lint warns on age.

3. **Report.** `Handoff: .tapps-mcp/session-handoff.md. Linear P0: <id|none>. brain_mirror: ok|skipped. session_end: ok|skipped. Next: tapps-continue-session`
