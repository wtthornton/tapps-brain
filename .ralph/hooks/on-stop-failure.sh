#!/bin/bash
# .ralph/hooks/on-stop-failure.sh
# StopFailure hook. Fires on rate limits and server errors.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
error_type=$(echo "$INPUT" | jq -r '.error_type // "unknown"' 2>/dev/null || echo "unknown")

echo "[$(date '+%H:%M:%S')] STOP FAILURE: $error_type" >> "$RALPH_DIR/live.log"

# Update status to reflect the error (TAP-680: no post-mv rm — preserve temp on mv failure)
if [[ -f "$RALPH_DIR/status.json" ]]; then
  local_tmp=$(mktemp "$RALPH_DIR/status.json.XXXXXX")
  if ! jq --arg err "$error_type" '.status = "error" | .exit_reason = $err' \
      "$RALPH_DIR/status.json" > "$local_tmp"; then
    rm -f "$local_tmp"
    echo "[$(date '+%H:%M:%S')] ERROR: on-stop-failure jq failed while updating status.json" >> "$RALPH_DIR/live.log"
  elif ! mv "$local_tmp" "$RALPH_DIR/status.json"; then
    echo "[$(date '+%H:%M:%S')] ERROR: on-stop-failure atomic write failed; temp preserved at $local_tmp" >> "$RALPH_DIR/live.log"
    exit 1
  fi
fi

exit 0
