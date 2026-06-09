---
name: tapps-handoff-session
user-invocable: true
model: claude-haiku-4-5-20251001
description: >-
  Write a structured cross-session handoff and close the TAPPS session
  lifecycle so the next chat can continue without a long paste. Use when
  ending a session, handing off to a fresh chat, or the user says hand
  off, save session state, or continue next time.
allowed-tools: mcp__tapps-mcp__tapps_session_end Bash
argument-hint: "[optional Linear issue id e.g. TAP-1234]"
disable-model-invocation: true
---

End the session with a durable handoff the next chat can load via `/tapps-continue-session`.

1. **Draft handoff (5–10 bullets).** From this session's work, write:
   - **Done** — what shipped or was verified
   - **Open** — in-progress or untested
   - **Next (P0)** — one concrete next action (prefer a Linear id if known)
   - **Blockers** — or `none`
   - **Verify** — commands to run first in the next session
   - **Success criterion** — one line

2. **Persist (file is canonical).** Write or overwrite `.tapps-mcp/session-handoff.md` using this shape:
   - Set **Updated** to the real current UTC time: run `date -u +%Y-%m-%dT%H:%M:%SZ` and paste the output — never use a placeholder like `T00:00:00Z`.
   - Optionally add **Git:** `<short-sha>` when inside a git repo (`git rev-parse --short HEAD`).

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
- ...

## Blockers
- ...

## Verify
- ...

## Success criterion
- ...
```

3. **Persist (brain, best-effort).** Priority order (file from step 2 is always canonical):

   | Priority | When | How |
   |----------|------|-----|
   | 1 (preferred MCP) | `tapps_memory` MCP available | `mcp__tapps-mcp__tapps_memory(action="save", key="session-handoff", tier="context", tags="handoff,cross-session", value="<plain-text bullets>")` |
   | 2 (CLI HTTP) | MCP unavailable; `TAPPS_MCP_MEMORY_BRAIN_HTTP_URL` + `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN` in shell | `uv run tapps-mcp memory save --key session-handoff --tier context --tags handoff,cross-session --value "<plain-text bullets>"` |
   | 3 (skip) | Brain offline | Skip silently — the markdown file is enough |

4. **Close lifecycle.** Best-effort session closure:
   - **Preferred:** `mcp__tapps-mcp__tapps_session_end()`
   - **CLI fallback** (MCP unavailable): `uv run tapps-mcp session-end` (requires same shell auth as step 3 row 2)
   Do not fail the handoff if either degrades.

5. **Report.** One line: `Handoff written: .tapps-mcp/session-handoff.md. Linear P0: <id|none>. session_end: ok|skipped. Next session: invoke /tapps-continue-session`
