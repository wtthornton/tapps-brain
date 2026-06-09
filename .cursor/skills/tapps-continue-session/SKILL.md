---
name: tapps-continue-session
description: >-
  Bootstrap a fresh session from the last handoff by reading session-handoff.md,
  optional Linear context, and TAPPS session start — without pasting a long
  manifesto. Use when the user says continue, pick up where we left off, resume,
  or start a new session on an existing task (optional TAP-#### argument).
mcp_tools:
  - tapps_session_start
  - linear_get_issue
---

Start work in a fresh context by assembling structured state.

1. **Session bootstrap.**
   - **Preferred:** Call `tapps_session_start()`. Note `compaction_rehydration` if present.
   - **CLI fallback** (MCP unavailable): Run `uv run tapps-mcp doctor --quick` and read `.tapps-mcp.yaml` for project context. Proceed without blocking.

2. **Load handoff (priority):** Read `.tapps-mcp/session-handoff.md`; else `uv run tapps-mcp memory get --key session-handoff`. Optional: `docs/NEXT_SESSION_PROMPT.md`, `docs/TAPPS_HANDOFF.md` (**Next:** section).

3. **Linear:** `get_issue(id=...)` when user or handoff names `TAP-####`; else use `linear-read` for lists.

4. **Emit ~15-line continue block:** P0, Done/Open/Blockers (compressed), Verify first, Success criterion. Warn if handoff **Updated** >7 days old.

5. Proceed on P0; do not ask for a re-paste when handoff exists.
