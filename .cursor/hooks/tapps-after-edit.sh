#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.27
# tapps-mcp-hook-content-sha: bc059b0a
# TappsMCP afterFileEdit hook (fire-and-forget)
# Reminds the agent to check quality after file edits.
INPUT=$(cat)
PY="import sys,json; d=json.load(sys.stdin); print(d.get('file_path') or d.get('file') or 'unknown')"
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
FILE=$(echo "$INPUT" | "$PYBIN" -c "$PY" 2>/dev/null)
echo "File edited: $FILE"
echo "Consider running tapps_quick_check to verify quality."
exit 0
