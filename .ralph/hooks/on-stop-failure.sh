#!/bin/bash
# .ralph/hooks/on-stop-failure.sh
# StopFailure hook. Fires on rate limits and server errors.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
error_type=$(echo "$INPUT" | jq -r '.error_type // "unknown"' 2>/dev/null || echo "unknown")

echo "[$(date '+%H:%M:%S')] STOP FAILURE: $error_type" >> "$RALPH_DIR/live.log"

# Update status to reflect the error
if [[ -f "$RALPH_DIR/status.json" ]]; then
  local_tmp=$(mktemp "$RALPH_DIR/status.json.XXXXXX")
  jq --arg err "$error_type" '.status = "error" | .exit_reason = $err' \
    "$RALPH_DIR/status.json" > "$local_tmp" \
    && mv "$local_tmp" "$RALPH_DIR/status.json"
  rm -f "$local_tmp" 2>/dev/null  # Clean up temp file if mv was skipped
fi

exit 0
