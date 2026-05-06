#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.10.9
# TappsMCP UserPromptSubmit hook (TAP-975)
# Re-surfaces pipeline state per user turn so long sessions don't drift.
# Reads two sidecars:
#   .tapps-mcp/.session-start-marker   — Unix epoch of last tapps_session_start
#   .tapps-mcp/.checklist-state.json   — last tapps_checklist outcome
# Stays SILENT when session_start was within 30 min AND no open checklist.
INPUT=$(cat)
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
SS_MARKER="$PROJECT_DIR/.tapps-mcp/.session-start-marker"
CL_STATE="$PROJECT_DIR/.tapps-mcp/.checklist-state.json"
NOW=$(date +%s)
NEED_SS=0
if [ ! -f "$SS_MARKER" ]; then
  NEED_SS=1
else
  SS=$(cat "$SS_MARKER" 2>/dev/null)
  if ! echo "$SS" | grep -Eq '^[0-9]+$'; then
    SS=0
  fi
  AGE=$((NOW - SS))
  # 1800s = 30 minute freshness window per TAP-975 AC.
  if [ "$AGE" -gt 1800 ]; then
    NEED_SS=1
  fi
fi
OPEN_CHECKLIST=""
if [ -f "$CL_STATE" ]; then
  PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
  OPEN_CHECKLIST=$("$PYBIN" -c "
import json
try:
    d=json.load(open('$CL_STATE'))
    if d.get('complete') is False:
        m=d.get('missing_required',[])
        if m:
            print('open: ' + ', '.join(m[:3]))
        else:
            print('open')
except Exception:
    pass
" 2>/dev/null)
fi
if [ "$NEED_SS" -eq 0 ] && [ -z "$OPEN_CHECKLIST" ]; then
  exit 0
fi
{
  echo "[TappsMCP] Pipeline-state reminder:"
  if [ "$NEED_SS" -eq 1 ]; then
    echo "  - tapps_session_start was not called within the last 30 min — call it before edits to refresh project context."
  fi
  if [ -n "$OPEN_CHECKLIST" ]; then
    echo "  - tapps_checklist last reported incomplete ($OPEN_CHECKLIST) — run /tapps-finish-task or address the missing tools."
  fi
} >&2
exit 0
