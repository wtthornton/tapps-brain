#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.42
# tapps-mcp-hook-content-sha: 8ac3fe5f
# TappsMCP afterFileEdit hook (fire-and-forget) — TAP-1330 import parity
# Detects external imports requiring tapps_lookup_docs. Advisory only.
INPUT=$(cat)
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
PARSED=$(TAPPS_HOOK_INPUT="$INPUT" "$PYBIN" - <<'PYEOF' 2>/dev/null
import os, json, re
from pathlib import Path

try:
    d = json.loads(os.environ.get("TAPPS_HOOK_INPUT", "{}"))
    ti = d.get("tool_input") or d.get("toolInput") or {}
    f = (
        d.get("file")
        or d.get("file_path")
        or ti.get("file_path")
        or ti.get("path")
        or ""
    )
    content = ti.get("content") or ti.get("new_string") or ""
    if not content and f:
        candidate = Path(f)
        if not candidate.is_file():
            for root in (
                os.environ.get("TAPPS_MCP_PROJECT_ROOT"),
                os.environ.get("TAPPS_PROJECT_ROOT"),
                os.environ.get("CURSOR_PROJECT_DIR"),
                os.getcwd(),
            ):
                if not root:
                    continue
                alt = Path(root) / f
                if alt.is_file():
                    candidate = alt
                    break
        if candidate.is_file():
            content = candidate.read_text(encoding="utf-8", errors="replace")
    print(f)
    libs: set[str] = set()
    if f.endswith((".py", ".pyi")):
        for m in re.finditer(
            r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", content, re.M
        ):
            libs.add(m.group(1))
    elif f.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        js_import = r"^\s*import[^'"]*['"]([^'"./][^'"]*)['"]"
        for m in re.finditer(js_import, content, re.M):
            libs.add(m.group(1).split("/")[0])
    print(",".join(sorted(libs)))
except Exception:
    print("")
    print("")
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
  *)
    if [ -n "$FILE" ] && [ "$FILE" != "unknown" ]; then
      echo "File edited: $FILE"
      echo "Consider running tapps_quick_check to verify quality."
    fi
    ;;
esac
exit 0
