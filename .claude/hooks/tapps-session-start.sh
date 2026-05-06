#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.10.9
# TappsMCP SessionStart hook (startup/resume)
# Directs the agent to call tapps_session_start as the first MCP action.
# TAP-1379: Short-circuits on subsequent fires within the same Claude session
# (resume/compact re-fire the SessionStart hook; emitting the REQUIRED prompt
# every time caused agents to re-call tapps_session_start ~23x per session).
INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
SENTINEL_DIR="${TAPPS_PROJECT_ROOT:-.}/.tapps-mcp"
if [ -n "$SID" ]; then
  SENTINEL="$SENTINEL_DIR/.session-start-fired-$SID"
  if [ -f "$SENTINEL" ]; then
    # Already prompted the agent for this Claude session; stay silent on resume.
    exit 0
  fi
  mkdir -p "$SENTINEL_DIR" 2>/dev/null || true
  : > "$SENTINEL" 2>/dev/null || true
fi
echo "REQUIRED: Call tapps_session_start() NOW as your first action."
echo "This initializes project context for all TappsMCP quality tools."
echo "Tools called without session_start will have degraded accuracy."
exit 0
