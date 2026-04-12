#!/usr/bin/env bash
# TappsMCP Stop hook - Memory Capture (Epic 34.5)
# Writes session quality data to .tapps-mcp/session-capture.json for
# persistence into shared memory on next session start.
# IMPORTANT: Must check stop_hook_active to prevent infinite loops.
INPUT=$(cat)
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
ACTIVE=$(echo "$INPUT" | "$PYBIN" -c   "import sys,json; d=json.load(sys.stdin); print(d.get('stop_hook_active','false'))"   2>/dev/null)
if [ "$ACTIVE" = "True" ] || [ "$ACTIVE" = "true" ]; then
  exit 0
fi
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
CAPTURE_DIR="$PROJECT_DIR/.tapps-mcp"
MARKER="$CAPTURE_DIR/.validation-marker"
VALIDATED="false"
if [ -f "$MARKER" ]; then
  VALIDATED="true"
fi
DATE=$("$PYBIN" -c "from datetime import date; print(date.today().isoformat())" 2>/dev/null   || date +%Y-%m-%d 2>/dev/null || echo "unknown")
FILES=$("$PYBIN" -c   "import subprocess,sys;r=subprocess.run(['git','diff','--name-only','HEAD'],capture_output=True,text=True,cwd='$PROJECT_DIR');print(len([f for f in r.stdout.strip().split(chr(10)) if f.endswith('.py') and f]))"   2>/dev/null || echo "0")
mkdir -p "$CAPTURE_DIR" 2>/dev/null || exit 0
"$PYBIN" -c "
import json,sys
data={'date':'$DATE','validated':$VALIDATED,'files_edited':int('$FILES' or '0')}
with open('$CAPTURE_DIR/session-capture.json','w') as f:
    json.dump(data,f)
" 2>/dev/null
exit 0
