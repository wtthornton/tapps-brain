#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.83
# tapps-mcp-hook-content-sha: 2aae887b
# TappsMCP PostToolUse hook — session-start sentinel writer.
# Writes .session-start-done-<SID> ONLY when tapps_session_start actually
# returned a success envelope, proving the *tool* ran (not merely that the
# SessionStart hook fired, and not merely that the *name* was called).
# TAP-7018: a tool_relocated pointer error (a retired-server registration
# calling back "this name moved") used to satisfy this gate on tool name
# alone, silently releasing every downstream quality tool with session_start
# never having actually run.
INPUT=$(cat)
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
if [ -z "$PYBIN" ]; then
  exit 0
fi
PARSED=$(echo "$INPUT" | "$PYBIN" -c   "import sys,json
try:
    d=json.load(sys.stdin)
    tool=d.get('tool_name') or d.get('toolName') or ''
    sid=d.get('session_id') or d.get('sessionId') or ''
    resp=d.get('tool_response') or d.get('toolResponse') or {}
    if isinstance(resp,str):
        try: resp=json.loads(resp)
        except Exception: resp={}
    ok=isinstance(resp,dict) and resp.get('success') is True and 'error' not in resp
    print(tool)
    print(sid)
    print('1' if ok else '0')
except Exception:
    print('')
    print('')
    print('0')" 2>/dev/null)
TOOL=$(echo "$PARSED" | sed -n '1p')
SID=$(echo "$PARSED" | sed -n '2p')
OK=$(echo "$PARSED" | sed -n '3p')
case "$TOOL" in
  *tapps_session_start) ;;
  *) exit 0 ;;
esac
[ -z "$SID" ] && exit 0
if [ "$OK" != "1" ]; then
  exit 0
fi
ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
mkdir -p "$ROOT/.tapps-mcp" 2>/dev/null
: > "$ROOT/.tapps-mcp/.session-start-done-$SID" 2>/dev/null
# Best-effort GC of sentinels left by prior Claude sessions (older than 1 day).
find "$ROOT/.tapps-mcp" -maxdepth 1 -name '.session-start-done-*' -mtime +1 -delete 2>/dev/null || true
exit 0
