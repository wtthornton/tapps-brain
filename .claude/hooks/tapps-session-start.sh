#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.16
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
# ADR-0005: Kill MCP server processes older than 2 hours to prevent zombie
# accumulation. Claude Code spawns a new tapps-mcp/docsmcp process per session
# but does not consistently reap old children — after several sessions this
# becomes a significant resource and Postgres connection leak.
# DO NOT REMOVE — see docs/adr/0005-mcp-server-zombie-cleanup-hook-on-session-start.md
if command -v ps &>/dev/null && command -v awk &>/dev/null; then
    OLD_PIDS=$(ps -eo pid,etimes,cmd 2>/dev/null | \
        awk '$2 > 7200 && /tapps-mcp|docsmcp/ && /serve/ {print $1}')
    if [ -n "$OLD_PIDS" ]; then
        echo "$OLD_PIDS" | xargs kill 2>/dev/null || true
    fi
fi
# TAP-1927: Pre-warm the brain tools-list cache so _negotiate_profile_locked
# can skip the live MCP tools/list round-trip on the first bridge call.
# Runs in the background (does not block session start) and is best-effort
# (curl failure leaves the cache absent; bridge falls through to live fetch).
if [ -n "${TAPPS_MCP_MEMORY_BRAIN_HTTP_URL:-}" ] && command -v curl &>/dev/null; then
    _BRAIN_PROFILE="${TAPPS_BRAIN_PROFILE:-}"
    _CACHE_DIR="${TAPPS_PROJECT_ROOT:-.}/.tapps-mcp"
    _SAFE_PROFILE=$(printf '%s' "$_BRAIN_PROFILE" | tr -c 'A-Za-z0-9_-' '_')
    _CACHE_FILE="$_CACHE_DIR/.brain-tools-list.${_SAFE_PROFILE}.json"
    mkdir -p "$_CACHE_DIR" 2>/dev/null || true
    _BRAIN_URL="${TAPPS_MCP_MEMORY_BRAIN_HTTP_URL%/}/v1/tools/list"
    if [ -n "$_BRAIN_PROFILE" ]; then
        _BRAIN_URL="${_BRAIN_URL}?profile=${_BRAIN_PROFILE}"
    fi
    curl -sf --max-time 1 "$_BRAIN_URL" -o "$_CACHE_FILE" 2>/dev/null &
fi
echo "REQUIRED: Call tapps_session_start() NOW as your first action."
echo "This initializes project context for all TappsMCP quality tools."
echo "Tools called without session_start will have degraded accuracy."
exit 0
