---
name: tapps-handoff-session
description: >-
  Write a structured cross-session handoff and close the TAPPS session
  lifecycle so the next chat can continue without a long paste. Use when
  ending a session, handing off to a fresh chat, or the user says hand
  off, save session state, or continue next time.
mcp_tools:
  - tapps_session_end
  - tapps_memory
---

End the session with a durable handoff the next chat loads via `tapps-continue-session`.

1. **Draft handoff (5–10 bullets):** Done, Open, Next (P0), Blockers, Verify commands, Success criterion (one line).

2. **Persist (file is canonical).** Write or overwrite `.tapps-mcp/session-handoff.md`:
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
   | 1 (preferred MCP) | `tapps_memory` MCP available | `tapps_memory(action="save", key="session-handoff", tier="context", tags="handoff,cross-session", value="<plain-text bullets>")` |
   | 2 (CLI HTTP) | MCP unavailable; `TAPPS_MCP_MEMORY_BRAIN_HTTP_URL` + `TAPPS_MCP_MEMORY_BRAIN_AUTH_TOKEN` in shell | `uv run tapps-mcp memory save --key session-handoff --tier context --tags handoff,cross-session --value "<plain-text bullets>"` |
   | 3 (skip) | Brain offline | Skip silently — the markdown file is enough |

4. **Close lifecycle.** Best-effort session closure:
   - **Preferred:** `tapps_session_end()`
   - **CLI fallback** (MCP unavailable): `uv run tapps-mcp session-end` (requires same shell auth as step 3 row 2)
   Do not fail the handoff if either degrades.

5. **Report.** `Handoff: .tapps-mcp/session-handoff.md. Linear P0: <id|none>. session_end: ok|skipped. Next: tapps-continue-session`
