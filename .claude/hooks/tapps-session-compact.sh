#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.42
# tapps-mcp-hook-content-sha: 9646bf5b
# TappsMCP SessionStart hook (compact)
# Re-injects TappsMCP context after context compaction.
INPUT=$(cat)
echo "[TappsMCP] Context was compacted — re-injecting TappsMCP awareness."
echo "Remember: use tapps_quick_check after editing Python files."
echo "Run tapps_validate_changed before declaring work complete."
PROJECT="${TAPPS_PROJECT_ROOT:-${CLAUDE_PROJECT_DIR:-.}}"
if command -v tapps-mcp >/dev/null 2>&1; then
  USAGE_HINT=$(tapps-mcp usage-gaps-hint --project-root "$PROJECT" 2>/dev/null || true)
  if [ -n "$USAGE_HINT" ]; then
    echo "TappsMCP prior-session reminder: $USAGE_HINT"
  fi
fi
exit 0
