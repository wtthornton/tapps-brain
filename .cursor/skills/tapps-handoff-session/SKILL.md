---
name: tapps-handoff-session
description: >-
  Write a structured cross-session handoff and close the TAPPS session
  lifecycle so the next chat can continue without a long paste. Use when
  ending a session, handing off to a fresh chat, or the user says hand
  off, save session state, or continue next time.
mcp_tools:
  - tapps_session_end
---

End the session with a durable handoff the next chat loads via `tapps-continue-session`.

1. **Draft handoff (5–10 bullets):** Done, Open, Next (P0), Blockers, Verify commands, Success criterion (one line).

**P0 gate.** Before writing the file: when **Open** has real items (not `none` / `- ...` placeholders), **Next (P0)** must name one concrete next action (prefer a Linear id). If P0 is missing, ask the user once — do not persist an incomplete handoff.

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

3. **Persist (brain mirror, best-effort).** The markdown file from step 2 is always canonical. `tapps_memory` is not an MCP tool (removed v3.12.0, TAP-1994) — use CLI only:

   | Priority | When | How |
   |----------|------|-----|
   | 1 (MCP) | TappsMCP tools available | `tapps_handoff_save(markdown=...)` with full handoff body; set `session_end=true` to close the flywheel |
   | 2 (CLI atomic) | Shell auth available; no MCP write | `uv run tapps-mcp handoff write --file .tapps-mcp/session-handoff.md` (lint + full-body brain mirror + optional `--session-end`) |
   | 3 (manual) | Brain HTTP reachable; atomic paths unavailable | `uv run tapps-mcp memory save --key session-handoff --tier context --tags handoff,cross-session --value "$(cat .tapps-mcp/session-handoff.md)"` — mirror the **full markdown body**, not a one-line agent summary |
   | 4 (skip) | Brain offline or auth missing | Skip silently — `.tapps-mcp/session-handoff.md` is enough |

4. **Close lifecycle.** Best-effort session closure:
   - **Preferred:** `tapps_session_end()`
   - **CLI fallback** (MCP unavailable): `uv run tapps-mcp session-end` (requires same shell auth as step 3 row 1)
   Do not fail the handoff if either degrades.

5. **Report.** `Handoff: .tapps-mcp/session-handoff.md. Linear P0: <id|none>. brain_mirror: ok|skipped. session_end: ok|skipped. Next: tapps-continue-session`
