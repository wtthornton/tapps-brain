#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.42
# tapps-mcp-hook-content-sha: 192f834b
# TappsMCP PostToolUse hook (Edit/Write) — TAP-1326 / TAP-1330
# Detects new external imports requiring tapps_lookup_docs. Advisory only;
# the Stop hook enforces the completion gate.
INPUT=$(cat)
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
PARSED=$(TAPPS_HOOK_INPUT="$INPUT" "$PYBIN" - <<'PYEOF' 2>/dev/null
import os, json, re
try:
    d = json.loads(os.environ.get('TAPPS_HOOK_INPUT', '{}'))
    ti = d.get('tool_input') or d.get('toolInput') or {}
    f = ti.get('file_path') or ti.get('path') or ''
    content = ti.get('content') or ti.get('new_string') or ''
    print(f)
    libs = set()
    if f.endswith(('.py', '.pyi')):
        for m in re.finditer(r'^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)', content, re.M):
            libs.add(m.group(1))
    elif f.endswith(('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs')):
        for m in re.finditer(r'''^\s*import[^'"]*['"]([^'"./][^'"]*)['"]''', content, re.M):
            libs.add(m.group(1).split('/')[0])
    print(','.join(sorted(libs)))
except Exception:
    print('')
    print('')
PYEOF
)
FILE=$(echo "$PARSED" | sed -n '1p')
LIBS=$(echo "$PARSED" | sed -n '2p')
case "$FILE" in
  *.py|*.pyi|*.ts|*.tsx|*.js|*.jsx|*.go|*.rs)
    echo "Edited: $FILE — run tapps_quick_check after this edit." >&2
    if [ -n "$LIBS" ]; then
      echo "Imports detected ($LIBS) — call tapps_lookup_docs(library=..., topic=...) before using those APIs in this session (TAP-1330)." >&2
    fi
    ;;
esac
exit 0
